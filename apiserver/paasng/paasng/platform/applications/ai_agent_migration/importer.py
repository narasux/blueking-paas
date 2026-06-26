# -*- coding: utf-8 -*-
# TencentBlueKing is pleased to support the open source community by making
# 蓝鲸智云 - PaaS 平台 (BlueKing - PaaS System) available.
# Copyright (C) Tencent. All rights reserved.
# Licensed under the MIT License (the "License"); you may not use this file except
# in compliance with the License. You may obtain a copy of the License at
#
#     http://opensource.org/licenses/MIT
#
# Unless required by applicable law or agreed to in writing, software distributed under
# the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
# either express or implied. See the License for the specific language governing permissions and
# limitations under the License.
#
# We undertake not to change the open source license (MIT license) applicable
# to the current version of the project delivered to anyone in the future.

"""Import and preflight logic for AI Agent application metadata migration.

本模块专负责"导入"与"预检”两种动作，被 management command
``migrate_ai_agent_metadata preflight`` / ``migrate_ai_agent_metadata import`` 调用。

架构约束（**强约束**）：

* 本模块**只依赖** ``helpers.py``，**禁止**导入 ``exporter.py``；
* 以保证"只部署导入能力"的场景不需要拖入导出侧依赖。

核心两阶段设计：

1. **preflight**：只读不写，产出 :class:`MigrationReport`，告诉运维"将会发生"什么变更；
2. **import**：先跳一遍 preflight 拿到报告，遇到 fail+conflicts 直接中断；否则以
   ``transaction.atomic()`` 按 application 粒度逐个写入。单个应用的异常不会传染到其他应用。
"""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

from paasng.bk_plugins.bk_plugins.models import BkPluginDistributor, BkPluginProfile, BkPluginTag
from paasng.core.tenant.constants import AppTenantMode
from paasng.core.tenant.utils import AppTenantInfo
from paasng.platform.agent_sandbox.models import Volume
from paasng.platform.applications.ai_agent_migration.helpers import (
    SCHEMA_VERSION,
    AppPayload,
    ImportOptions,
    MetadataValidationError,
    MigrationPayload,
    MigrationReport,
    ObjectAction,
    diff_dicts,
    extract_app_update_fields,
    extract_module_update_fields,
    get_app_code,
    get_optional_by_name,
    get_wl_app_type,
    is_sensitive_placeholder,
    parse_value,
    read_app_update_fields,
    read_module_update_fields,
)
from paasng.platform.applications.constants import ApplicationType
from paasng.platform.applications.models import Application, ModuleEnvironment
from paasng.platform.applications.signals import post_create_application
from paasng.platform.applications.utils import create_application, create_default_module
from paasng.platform.bkapp_model.models import ModuleDeployHook, ModuleProcessSpec, ProcessSpecEnvOverlay
from paasng.platform.modules.constants import SourceOrigin
from paasng.platform.modules.manager import ModuleInitializer, make_engine_app_name
from paasng.platform.modules.models import AppBuildPack, AppSlugBuilder, AppSlugRunner, BuildConfig, Module

logger = logging.getLogger(__name__)


def import_ai_agent_metadata(payload: MigrationPayload, options: ImportOptions | None = None) -> MigrationReport:
    """把 payload 导入到当前环境，面向 management command 的顶层入口。

    :param payload: 已加载的迁移 payload（一般由 :func:`load_payload` 读取产生）。
    :param options: 导入选项；为空时使用 :class:`ImportOptions` 默认值（策略=fail / dry_run=False）。
    """
    importer = AiAgentMetadataImporter(options or ImportOptions())
    return importer.import_payload(payload)


def preflight_ai_agent_metadata(payload: MigrationPayload, options: ImportOptions | None = None) -> MigrationReport:
    """仅运行预检，不写库，面向 management command 的顶层入口。

    会强制设置 ``options.dry_run = True``，避免调用方忘记设置。
    """
    opts = options or ImportOptions()
    opts.dry_run = True
    return AiAgentMetadataImporter(opts).preflight(payload)


class AiAgentMetadataImporter:
    """AI Agent 应用元数据导入器与预检检查器。

    入口与逻辑分层：

    * :meth:`preflight` / :meth:`import_payload`：外部入口，只依赖 :data:`ImportOptions`，产出 :class:`MigrationReport`。
    * ``_validate_payload``：静态结构校验，任何不足都会招致 :class:`MetadataValidationError`。
    * ``_plan_*``：预检阶段逻辑，只读 DB，不写入。
    * ``_import_*`` / ``_sync_*`` / ``_create_*`` / ``_update_*``：实际写库逻辑。

    双阶段设计所以存在：在 fail 策略下能“发现冲突立即中断”，在 update 策略下可以在预检报告里
    看到会被覆盖哪些字段。
    """

    def __init__(self, options: ImportOptions):
        self.options = options

    # ------------------------------------------------------------------ public

    def preflight(self, payload: MigrationPayload) -> MigrationReport:
        """只阅不写，产出“将会发生”动作清单。

        会依次调用 ``_validate_payload`` 与 ``_plan_application`` 类方法，所有冲突与差异都被
        记入 :class:`MigrationReport`。
        """
        self._validate_payload(payload)
        report = MigrationReport(total=len(payload.get("applications", [])))
        for app_payload in payload.get("applications", []):
            self._plan_application(app_payload, report)
        return report

    def import_payload(self, payload: MigrationPayload) -> MigrationReport:
        """实际导入 payload 的主入口。

        流程如下：

        1. 静态结构校验 → ``_validate_payload``；
        2. 若 ``options.dry_run`` 为 True，跳转走 :meth:`preflight` 后返回；
        3. 跳一遍 preflight 拿到初始报告；若是 fail 策略且存在 conflicts，直接中断；
        4. 重置报告中 “预检阶段” 填充的动作与计数（warnings/total 除外），避免与实际变更重复计数；
        5. 逐个 Application 走 ``transaction.atomic()`` 写入，单应用失败记到 ``failed`` 后继续处理下一个。
        """
        self._validate_payload(payload)
        if self.options.dry_run:
            return self.preflight(payload)

        # 先走一遍 preflight 获得动作清单与 warnings；如果是 fail 策略 + conflicts 不为空，
        # 直接中断，以严格保障“不覆盖什么”的语义。
        report = self.preflight(payload)
        if report.has_blocking_conflicts and self.options.conflict_strategy == "fail":
            report.warnings.append("导入被阻止：预检发现冲突且冲突策略为 fail。")
            return report

        # 保留 warnings/total，清空其他“预检阶段”填进去的列表，重新计算“实际发生”的动作。
        # 原因：预检阶段预估的 created/updated 与实际写入阶段产生的动作可能不一致（如中间有其他并发变更），
        # 以“实际发生”为准。
        warnings = list(report.warnings)
        total = report.total
        report.created.clear()
        report.updated.clear()
        report.conflicts.clear()
        report.skipped.clear()
        report.succeeded.clear()
        report.failed.clear()
        report.warnings = warnings
        report.total = total

        for app_payload in payload.get("applications", []):
            app_code = get_app_code(app_payload)
            try:
                # **事务粒度 = 单个 Application**：保证单应用出错不污染其他已写入的应用。
                with transaction.atomic():
                    action = self._import_application(app_payload)
            except Exception as e:
                logger.exception("failed to import AI Agent application: %s", app_code)
                report.failed[app_code] = str(e)
            else:
                if action.action == "skip":
                    report.skipped.append(app_code)
                else:
                    report.succeeded.append(app_code)
                    report.add_action(action)
        return report

    # ----------------------------------------------------------------- private

    @staticmethod
    def _validate_payload(payload: MigrationPayload) -> None:  # noqa: C901 PLR0912
        """对 payload 做静态结构校验。任何不满足都招致 :class:`MetadataValidationError`。

        校验范围：

        1. payload 必须是 dict；
        2. ``schema_version`` 必须与 :data:`SCHEMA_VERSION` 严格相等，避免跨版本导入；
        3. ``applications`` 必须是非空列表；
        4. 每个 application 必须有 ``code`` 且 ``is_ai_agent_app=True``，避免误用本工具导入普通应用；
        5. 必须且仅能有一个默认模块（与 BkPaaS 平台语义一致）；
        6. 每个模块必须有名字与非空环境列表，每个环境必须有 environment 名。

        这里是“动作前的防御”，帮助在写入 DB 之前抦截明显质量问题的 payload。
        """
        if not isinstance(payload, dict):
            raise MetadataValidationError("导入文件必须是 JSON 对象。")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise MetadataValidationError(f"不支持的 schema_version: {payload.get('schema_version')}")
        if "applications" not in payload or not isinstance(payload["applications"], list):
            raise MetadataValidationError("导入文件缺少 applications 列表。")
        for index, app_payload in enumerate(payload["applications"]):
            if not isinstance(app_payload, dict):
                raise MetadataValidationError(f"applications[{index}] 必须是 JSON 对象。")
            app_data = app_payload.get("application")
            if not app_data or not app_data.get("code"):
                raise MetadataValidationError(f"applications[{index}] 缺少 application.code。")
            if app_data.get("is_ai_agent_app") is not True:
                # 强制要求带 ``is_ai_agent_app=True`` 是为了防止误用本工具导入任意普通应用。
                raise MetadataValidationError(f"应用 {app_data.get('code')} 不是 AI Agent 导出数据。")
            modules = app_payload.get("modules")
            if not isinstance(modules, list) or not modules:
                raise MetadataValidationError(f"应用 {app_data.get('code')} 缺少模块数据。")
            default_modules = [item for item in modules if item.get("module", {}).get("is_default")]
            if len(default_modules) != 1:
                raise MetadataValidationError(f"应用 {app_data.get('code')} 必须且只能包含一个默认模块。")
            for module_index, module_payload in enumerate(modules):
                module_data = module_payload.get("module")
                if not module_data or not module_data.get("name"):
                    raise MetadataValidationError(
                        f"应用 {app_data.get('code')} 的 modules[{module_index}] 缺少 module.name。"
                    )
                environments = module_payload.get("environments")
                if not isinstance(environments, list) or not environments:
                    raise MetadataValidationError(
                        f"应用 {app_data.get('code')} 模块 {module_data.get('name')} 缺少环境数据。"
                    )
                for env_index, env_payload in enumerate(environments):
                    env_data = env_payload.get("environment")
                    if not env_data or not env_data.get("environment"):
                        raise MetadataValidationError(
                            f"应用 {app_data.get('code')} 模块 {module_data.get('name')} 的 "
                            f"environments[{env_index}] 缺少环境名称。"
                        )

    def _plan_application(self, app_payload: AppPayload, report: MigrationReport) -> None:
        """预检单个应用，生成会被记录到报告中的动作。

        判断分支：

        * 目标不存在 → ``create``；
        * 目标存在但不是 AI Agent 应用 → ``conflict``（不允许跨类型覆盖）；
        * 存在同名 AI Agent 应用：
          - skip 策略 → ``skip``；
          - 有差异且 fail 策略 → ``conflict``；有差异且 update 策略 → ``update``；
          - 无差异 → ``skip``（不论何种策略都是 skip，避免多余写入）。

        这里仅在默认模块领域产生主体动作；子资源预检交给 ``_plan_module_dependencies``。
        """
        app_code = get_app_code(app_payload)
        try:
            existing_app = Application.objects.filter(code=app_code).first()
            if not existing_app:
                report.add_action(ObjectAction("create", "Application", app_code, "目标环境不存在应用"))
                self._plan_module_dependencies(app_payload, report)
                return

            if not existing_app.is_ai_agent_app:
                # 跨类型覆盖是高危动作，本工具一律拒绝，交由运维人工处理。
                report.add_action(
                    ObjectAction("conflict", "Application", app_code, "目标环境存在同 code 的非 AI Agent 应用")
                )
                return

            # 以主表字段白名单计算 diff。这里采用“单向” diff（只看 payload 中出现的字段），
            # 避免目标环境多出的旁支字段丢入报告。
            diffs = diff_dicts(
                extract_app_update_fields(app_payload["application"]),
                read_app_update_fields(existing_app),
            )
            if self.options.conflict_strategy == "skip":
                report.add_action(ObjectAction("skip", "Application", app_code, "目标环境已存在应用"))
            elif diffs:
                action = "conflict" if self.options.conflict_strategy == "fail" else "update"
                report.add_action(
                    ObjectAction(action, "Application", app_code, "目标环境已存在且字段有差异", {"diffs": diffs})
                )
            else:
                report.add_action(ObjectAction("skip", "Application", app_code, "目标环境已存在且无差异"))

            self._plan_module_dependencies(app_payload, report)
        except Exception as e:  # noqa: BLE001
            report.failed[app_code] = str(e)

    def _plan_module_dependencies(self, app_payload: AppPayload, report: MigrationReport) -> None:
        """预检与应用关联的模块、环境集群映射、buildpack/builder/runner 及插件依赖。

        这里调用了多个 ``_plan_*`` 与 ``_plan_*_dependencies``，多个动作会追加到同一份报告中。
        """
        app_code = get_app_code(app_payload)
        app = Application.objects.filter(code=app_code).first()
        for module_payload in app_payload.get("modules", []):
            module_data = module_payload.get("module", {})
            module_name = module_data.get("name")
            # 模块粒度的 object_id 使用 "app_code:module_name"，不使用 module.id，以便人工快速定位。
            object_id = f"{app_code}:{module_name}"
            module = app.modules.filter(name=module_name).first() if app else None
            if not module:
                report.add_action(ObjectAction("create", "Module", object_id, "目标环境不存在模块"))
            else:
                diffs = diff_dicts(extract_module_update_fields(module_data), read_module_update_fields(module))
                if diffs and self.options.conflict_strategy == "fail":
                    report.add_action(
                        ObjectAction("conflict", "Module", object_id, "目标环境模块字段有差异", {"diffs": diffs})
                    )
                elif diffs:
                    report.add_action(
                        ObjectAction("update", "Module", object_id, "目标环境模块字段有差异", {"diffs": diffs})
                    )
                else:
                    report.add_action(ObjectAction("skip", "Module", object_id, "目标环境模块已存在且无差异"))

            for env_payload in module_payload.get("environments", []):
                env_name = env_payload.get("environment", {}).get("environment")
                if env_name and env_name not in self.options.env_cluster_mapping:
                    # 未配置集群映射不会中断导入，但一定会产生 warning——避免运维被默认分配策略“默默”决定集群。
                    report.warnings.append(f"环境 {object_id}:{env_name} 未配置集群映射，将使用目标环境默认分配策略。")
            build_config = module_payload.get("build_config")
            if build_config:
                self._plan_runtime_dependencies(build_config, object_id, report)
        self._plan_plugin_dependencies(app_payload.get("plugin") or {}, app_code, report)

    @staticmethod
    def _plan_plugin_dependencies(plugin_data: dict[str, Any], app_code: str, report: MigrationReport) -> None:
        """预检插件分类、使用方是否在目标环境存在。

        不存在时仅产生 warning，实际导入时会变为“置空 tag”/“跳过使用方”，避免中断主流程。
        """
        profile_data = plugin_data.get("profile") or {}
        tag_code_name = profile_data.get("tag_code_name")
        if tag_code_name and not BkPluginTag.objects.filter(code_name=tag_code_name).exists():
            report.warnings.append(f"应用 {app_code} 引用的插件分类不存在: {tag_code_name}，导入时将置为空。")

        missing_distributors = [
            code_name
            for code_name in plugin_data.get("distributor_code_names") or []
            if not BkPluginDistributor.objects.filter(code_name=code_name).exists()
        ]
        if missing_distributors:
            report.warnings.append(
                f"应用 {app_code} 引用的插件使用方不存在: {', '.join(missing_distributors)}，导入时将跳过。"
            )

    @staticmethod
    def _plan_runtime_dependencies(build_config: dict[str, Any], object_id: str, report: MigrationReport) -> None:
        """预检运行时依赖： builder / runner / buildpack。

        不存在仅产生 warning。实际导入时，不存在的名字会被 :func:`get_optional_by_name`
        返回 None，对应字段会保持 NULL，由运维后续手动补齐。
        """
        builder_name = build_config.get("buildpack_builder_name")
        runner_name = build_config.get("buildpack_runner_name")
        if builder_name and not AppSlugBuilder.objects.filter(name=builder_name).exists():
            report.warnings.append(f"模块 {object_id} 引用的构建镜像不存在: {builder_name}")
        if runner_name and not AppSlugRunner.objects.filter(name=runner_name).exists():
            report.warnings.append(f"模块 {object_id} 引用的运行镜像不存在: {runner_name}")
        missing_buildpacks = [
            name
            for name in build_config.get("buildpack_names") or []
            if not AppBuildPack.objects.filter(name=name).exists()
        ]
        if missing_buildpacks:
            report.warnings.append(f"模块 {object_id} 引用的 buildpack 不存在: {', '.join(missing_buildpacks)}")

    def _import_application(self, app_payload: AppPayload) -> ObjectAction:
        """实际导入单个应用。返回 :class:`ObjectAction` 描述本次实际发生的主体动作。

        处理分支：

        * skip 策略 + 已存在 → 返回 skip，不调 sync_*；
        * 存在同名非 AI Agent 应用 → 招出 MetadataValidationError（以 fail 姿态中断单应用事务）；
        * 存在同名 AI Agent 应用：
          - update 策略 → 调 _update_application 覆盖差异字段；
          - fail 策略 且 有差异 → 招出 MetadataValidationError（预检阶段未拦下的并发冲突马上报错）；
          - 无差异 → 仅记录为 update + “继续同步关联元数据”，以便关联资源能得到同步；
        * 不存在 → 调用 _create_application 从零创建。

        创建后一定会调用 sync_modules / sync_plugin / sync_agent_sandbox，让所有依赖资源都得到处理。
        """
        app_data = app_payload["application"]
        app_code = app_data["code"]
        existing_app = Application.objects.filter(code=app_code).first()
        if existing_app and self.options.conflict_strategy == "skip":
            # skip 策略下，存在即 skip。**这个 skip 会使 sync_modules 等子资源也被跳过**。
            return ObjectAction("skip", "Application", app_code, "目标环境已存在应用")

        if existing_app:
            if not existing_app.is_ai_agent_app:
                raise MetadataValidationError(f"目标环境存在同 code 的非 AI Agent 应用: {app_code}")
            app = existing_app
            diffs = diff_dicts(extract_app_update_fields(app_data), read_app_update_fields(app))
            if self.options.conflict_strategy == "update":
                self._update_application(app, app_data)
                action = ObjectAction("update", "Application", app_code, "更新已存在应用", {"diffs": diffs})
            elif diffs:
                # fail 策略下遇到差异必须中断。这个分支主要防御预检与导入间发生的并发变更。
                raise MetadataValidationError(f"目标环境应用字段存在差异: {app_code}")
            else:
                action = ObjectAction(
                    "update", "Application", app_code, "目标环境应用已存在且无差异，继续同步关联元数据"
                )
        else:
            app = self._create_application(app_data)
            action = ObjectAction("create", "Application", app_code, "创建新应用")

        # 应用主体处理完后，依次同步三类关联资源。
        # 这三个 sync_* 的顺序不可随意调换：module 创建是插件/沙箱卷处理的前提。
        self._sync_modules(app, app_payload)
        self._sync_plugin(app, app_payload.get("plugin") or {})
        self._sync_agent_sandbox(app, app_payload.get("agent_sandbox") or {})
        return action

    def _create_application(self, app_data: dict[str, Any]) -> Application:
        """创建全新应用。重用平台已有的 :func:`create_application` 以保证 OAuth/IAM 等内部初始化不遗漏。

        operator 优先级：``options.operator`` > ``creator`` > ``owner`` > ``"admin"``，
        避免跨环境 user 不存在时创建失败。
        """
        operator = self.options.operator or app_data.get("creator") or app_data.get("owner") or "admin"
        # 租户模式优先从 payload 取，默认 SINGLE；tenant_id 默认 "system" 以保证创建能走通。
        tenant_info = AppTenantInfo(
            app_tenant_mode=AppTenantMode(app_data.get("app_tenant_mode") or AppTenantMode.SINGLE.value),
            app_tenant_id=app_data.get("app_tenant_id") or "",
            tenant_id=app_data.get("tenant_id") or "system",
        )
        app = create_application(
            code=app_data["code"],
            name=app_data.get("name") or app_data["code"],
            name_en=app_data.get("name_en") or app_data.get("name") or app_data["code"],
            app_type=app_data.get("type") or ApplicationType.DEFAULT.value,
            operator=operator,
            is_plugin_app=bool(app_data.get("is_plugin_app")),
            is_ai_agent_app=True,
            app_tenant_info=tenant_info,
        )
        # ``create_application`` 只创建主体 + 必要初始化；还需“补齐主体字段”以贴合 payload。
        self._update_application(app, app_data, creating=True)
        return app

    def _update_application(self, app: Application, app_data: dict[str, Any], creating: bool = False) -> None:
        """按 :data:`APP_UPDATE_FIELDS` 差量覆盖主体字段。

        仅在字段值不一致时才调用 ``setattr`` + ``save(update_fields=...)``，避免无谓起事件/信号。
        在 ``creating=True`` 且 ``options.send_create_signal=True`` 时，补发一次
        ``post_create_application`` 以触发可能被目标环境重用的业务接收器。
        """
        update_fields: list[str] = []
        for field_name, value in extract_app_update_fields(app_data).items():
            mapped_value = self._map_field(field_name, value)
            if getattr(app, field_name) != mapped_value:
                setattr(app, field_name, mapped_value)
                update_fields.append(field_name)
        if update_fields:
            # 额外带上 "updated"（更新时间），以便上层可以看到近期被迁移过的记录。
            app.save(update_fields=[*update_fields, "updated"])
        if creating and self.options.send_create_signal:
            post_create_application.send(sender=self.__class__, application=app)

    def _sync_modules(self, app: Application, app_payload: AppPayload) -> None:
        """同步与应用关联的所有模块，包含环境、构建配置、进程编排与部署钩子。

        逻辑双位组：

        * 判断模块是否存在，不存在则创建；存在且策略为 update 时补差；
        * 所有模块都走一遍子资源 sync，以保证环境/build_config 等与 payload 对齐。

        在首次创建、且该模块还未关联任何环境时，调用 :class:`ModuleInitializer` 一次性
        创建 stag/prod 双环境。集群分配依赖 ``options.env_cluster_mapping``。
        """
        for module_payload in app_payload.get("modules", []):
            module_data = module_payload["module"]
            module = app.modules.filter(name=module_data["name"]).first()
            if not module:
                module = self._create_module(app, module_data)
            elif self.options.conflict_strategy == "update":
                self._update_module(module, module_data)

            if not module.envs.exists():
                # 如果是首次创建不包含环境的模块（如 create_default_module 不会同时创建 stag/prod），
                # 这里补上环境 + EngineApp。使用与该模块创建路径一致的 ModuleInitializer，以保证 wl_app 一致。
                ModuleInitializer(module).create_engine_apps(env_cluster_names=self.options.env_cluster_mapping)
            self._sync_module_envs(module, module_payload)
            self._sync_build_config(module, module_payload.get("build_config"))
            self._sync_process_specs(module, module_payload.get("process_specs") or [])
            self._sync_deploy_hooks(module, module_payload.get("deploy_hooks") or [])

    def _create_module(self, app: Application, module_data: dict[str, Any]) -> Module:
        """创建模块。区分默认模块与非默认模块两条路径。

        默认模块走 :func:`create_default_module` 以复用平台初始化逻辑（例如 source_origin、
        默认源码模板等）；如果目标上已存在默认模块（应用创建时会顺带创建一个），则不重复创建、
        仅同步字段。

        非默认模块直接走 ``Module.objects.create``，使用与主应用一致的 region/tenant_id 作为回退。
        """
        if module_data.get("is_default"):
            module = app.modules.filter(is_default=True).first()
            if module:
                self._update_module(module, module_data)
                return module
            module = create_default_module(
                app,
                language=module_data.get("language") or app.language,
                source_init_template=module_data.get("source_init_template") or "",
                source_origin=SourceOrigin(module_data.get("source_origin") or SourceOrigin.AI_AGENT.value),
            )
        else:
            module = Module.objects.create(
                application=app,
                name=module_data["name"],
                is_default=False,
                language=module_data.get("language") or app.language,
                source_init_template=module_data.get("source_init_template") or "",
                source_origin=module_data.get("source_origin") or SourceOrigin.AI_AGENT.value,
                creator=module_data.get("creator") or app.creator,
                owner=module_data.get("owner") or app.owner,
                # region 走映射，避免跨环境 region 名不一致导致外键失效。
                region=self._map_region(module_data.get("region") or app.region),
                tenant_id=module_data.get("tenant_id") or app.tenant_id,
            )
        # 创建后统一走一遍 _update_module，以补齐上面未处理的字段（exposed_url_type 等）。
        self._update_module(module, module_data)
        return module

    def _update_module(self, module: Module, module_data: dict[str, Any]) -> None:
        """按较严格的白名单差量覆盖模块字段。注意 **不包含** ``name`` 字段——
        模块名是识别器，不允许跨环境改名。
        """
        fields = (
            "is_default",
            "language",
            "source_init_template",
            "source_origin",
            "source_type",
            "source_repo_id",
            "exposed_url_type",
            "user_preferred_root_domain",
            "last_deployed_date",
            "creator",
            "owner",
            "region",
            "tenant_id",
        )
        update_fields: list[str] = []
        for field_name in fields:
            if field_name not in module_data:
                continue
            value = self._map_field(field_name, module_data[field_name])
            if getattr(module, field_name) != value:
                setattr(module, field_name, value)
                update_fields.append(field_name)
        if update_fields:
            module.save(update_fields=[*update_fields, "updated"])

    def _sync_module_envs(self, module: Module, module_payload: dict[str, Any]) -> None:
        """同步模块下的 ModuleEnvironment 与 EngineApp。

        * 存在则仅补上 ``is_offlined``（其余字段不应跨环境跨集群覆盖）；
        * 不存在则走 :class:`ModuleInitializer` 创建 EngineApp，名字采用
          ``make_engine_app_name`` 以保证与平台初始化逻辑一致。

        EngineApp 本身不在 payload 中被覆盖（上一侧 exporter 注释已说明），本函数只负责创建。
        """
        for env_payload in module_payload.get("environments", []):
            env_data = env_payload.get("environment") or {}
            env_name = env_data.get("environment")
            if not env_name:
                continue
            env = module.envs.filter(environment=env_name).first()
            if not env:
                expected_name = make_engine_app_name(module, module.application.code, env_name)
                # 这里调用了 ModuleInitializer 的 "_" 前缀方法：在平台现有接口中这是唯一可复用的
                # “按期望名创建 EngineApp”路径，接受一定耦合以避免重复实现同一逻辑。
                engine_app = ModuleInitializer(module)._get_or_create_engine_app(
                    expected_name, app_type=get_wl_app_type(module.application)
                )
                env = ModuleEnvironment.objects.create(
                    application=module.application,
                    module=module,
                    engine_app=engine_app,
                    environment=env_name,
                    tenant_id=env_data.get("tenant_id") or module.tenant_id,
                )
            update_fields: list[str] = []
            if "is_offlined" in env_data and env.is_offlined != env_data["is_offlined"]:
                env.is_offlined = env_data["is_offlined"]
                update_fields.append("is_offlined")
            if update_fields:
                env.save(update_fields=[*update_fields, "updated"])

    def _sync_build_config(self, module: Module, data: dict[str, Any] | None) -> None:
        """同步 BuildConfig。包括主体字段与三个名字引用（builder/runner/buildpacks）。

        几个要点：

        * **遇到占位符则跳过该字段**：防止把脱敏后的占位符写回 DB；
        * builder/runner 按名查找，不存在则置空（与 _plan_runtime_dependencies 中的 warning 对应）；
        * buildpacks 使用 ``set()`` 覆盖，避免遗留旧应用未使用的 buildpack 映射。
        """
        if not data:
            return
        cfg = BuildConfig.objects.get_or_create_by_module(module)
        fields = (
            "build_method",
            "dockerfile_path",
            "docker_build_args",
            "image_repository",
            "image_credential_name",
            "tag_options",
            "use_bk_ci_pipeline",
            "tenant_id",
        )
        update_fields: list[str] = []
        for field_name in fields:
            # 二重防护：只同步 payload 中给出的字段，且不写入占位符。
            if field_name not in data or is_sensitive_placeholder(data[field_name]):
                continue
            value = data[field_name]
            if getattr(cfg, field_name) != value:
                setattr(cfg, field_name, value)
                update_fields.append(field_name)

        builder = get_optional_by_name(AppSlugBuilder, data.get("buildpack_builder_name"))
        runner = get_optional_by_name(AppSlugRunner, data.get("buildpack_runner_name"))
        if cfg.buildpack_builder != builder:
            cfg.buildpack_builder = builder
            update_fields.append("buildpack_builder")
        if cfg.buildpack_runner != runner:
            cfg.buildpack_runner = runner
            update_fields.append("buildpack_runner")
        if update_fields:
            # ``dict.fromkeys`` 去重，避免同一字段被重复 push 造成 update_fields 出现重复项。
            cfg.save(update_fields=[*dict.fromkeys(update_fields), "updated"])

        # 名字反查后用 set() 覆盖；buildpack 仅使用名字存在的那些，不存在的被丢弃。
        buildpacks = list(AppBuildPack.objects.filter(name__in=data.get("buildpack_names") or []))
        cfg.buildpacks.set(buildpacks)

    def _sync_process_specs(self, module: Module, specs: list[dict[str, Any]]) -> None:
        """同步进程编排及其环境覆写。遇到占位符不同步该 spec。

        使用 ``update_or_create`` 按 ``(module, name)`` 唯一键幂等同步主体；
        env_overlays 按 ``(spec, environment_name)`` 同步。

        spec 中任一字段为占位符都会跳过整个 spec，避免部分脱敏字段被写为占位符。
        """
        for raw_spec_data in specs:
            spec_data = dict(raw_spec_data)
            env_overlays = spec_data.pop("env_overlays", [])
            if any(is_sensitive_placeholder(value) for value in spec_data.values()):
                continue
            # ``parse_value`` 负责把 ISO datetime 还原为 datetime；同步时必要。
            defaults = {key: parse_value(value) for key, value in spec_data.items() if key not in {"name"}}
            defaults.setdefault("tenant_id", module.tenant_id)
            spec, _ = ModuleProcessSpec.objects.update_or_create(
                module=module, name=spec_data["name"], defaults=defaults
            )
            for overlay_data in env_overlays:
                ProcessSpecEnvOverlay.objects.update_or_create(
                    proc_spec=spec,
                    environment_name=overlay_data["environment_name"],
                    defaults={
                        key: parse_value(value) for key, value in overlay_data.items() if key != "environment_name"
                    },
                )

    def _sync_deploy_hooks(self, module: Module, hooks: list[dict[str, Any]]) -> None:
        """同步部署钩子，以 ``(module, type)`` 为幂等键。

        仅会同步 payload 里有的 hook；目标环境多出的旧 hook 不会被删除，避免误删。
        """
        for hook_data in hooks:
            if any(is_sensitive_placeholder(value) for value in hook_data.values()):
                continue
            defaults = {key: parse_value(value) for key, value in hook_data.items() if key != "type"}
            defaults.setdefault("tenant_id", module.tenant_id)
            ModuleDeployHook.objects.update_or_create(module=module, type=hook_data["type"], defaults=defaults)

    def _sync_plugin(self, app: Application, plugin_data: dict[str, Any]) -> None:
        """同步插件 Profile、分类与使用方。非插件应用下本函数会是空调用。

        * Profile 使用 ``get_or_create_by_application`` 获取或创建；字段同步遵循“遇到占位符则跳过”原则。
        * tag 以 ``code_name`` 引用。目标环境不存在该 tag 时置空，与 _plan_plugin_dependencies 的 warning 对齐。
        * 使用方仅补充关联关系，**不会从现有使用方中移除应用**，避免误删。
        """
        profile_data = plugin_data.get("profile")
        if profile_data:
            profile, _ = BkPluginProfile.objects.get_or_create_by_application(app)
            fields = (
                "introduction",
                "contact",
                "api_gw_name",
                "api_gw_id",
                "api_gw_last_synced_at",
                "pre_distributor_codes",
                "owner",
                "region",
                "tenant_id",
            )
            update_fields: list[str] = []
            for field_name in fields:
                if field_name not in profile_data or is_sensitive_placeholder(profile_data[field_name]):
                    continue
                value = self._map_field(field_name, profile_data[field_name])
                if getattr(profile, field_name) != value:
                    setattr(profile, field_name, value)
                    update_fields.append(field_name)
            tag_code_name = profile_data.get("tag_code_name")
            tag = BkPluginTag.objects.filter(code_name=tag_code_name).first() if tag_code_name else None
            if profile.tag != tag:
                profile.tag = tag
                update_fields.append("tag")
            if update_fields:
                profile.save(update_fields=[*update_fields, "updated"])

        for distributor in BkPluginDistributor.objects.filter(
            code_name__in=plugin_data.get("distributor_code_names") or []
        ):
            # ``add`` 是幂等操作，重复导入不会造成重复关联。
            distributor.plugins.add(app)

    @staticmethod
    def _sync_agent_sandbox(app: Application, sandbox_data: dict[str, Any]) -> None:
        """同步沙箱卷元数据。卷底层 CFS 文件内容不在本工具范围内，需要运维单独同步。

        使用 ``(application, name)`` 作为幂等键；仅同步 payload 中的卷，不会删除目标多出的卷。
        """
        for volume_data in sandbox_data.get("volumes") or []:
            Volume.objects.update_or_create(
                application=app,
                name=volume_data["name"],
                defaults={
                    "display_name": volume_data.get("display_name") or "",
                    "deleted_at": parse_value(volume_data.get("deleted_at")),
                    "tenant_id": volume_data.get("tenant_id") or app.tenant_id,
                },
            )

    # --------------------------------------------------------------- mapping

    def _map_field(self, field_name: str, value: Any) -> Any:
        """按字段名给 payload 中的值应用跨环境映射。

        仅覆盖两个字段：

        * ``region``：走 :meth:`_map_region`；
        * ``user_preferred_root_domain``：仅在命中 ``root_domain_mapping`` 时才替换，未命中保留原值。

        其他字段不动，仅走 :func:`parse_value` 还原 datetime 等。
        """
        value = parse_value(value)
        if field_name == "region":
            return self._map_region(value)
        if field_name == "user_preferred_root_domain" and value in self.options.root_domain_mapping:
            return self.options.root_domain_mapping[value]
        return value

    def _map_region(self, value: Any) -> Any:
        """如果 ``options.region_mapping`` 中配置了该值，返回映射后的 region；否则原样返回。"""
        if value in self.options.region_mapping:
            return self.options.region_mapping[value]
        return value
