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

"""Export-only logic for AI Agent application metadata migration.

本模块专负责"导出"一侧的逻辑，被 management command
``migrate_ai_agent_metadata export`` 调用。

架构约束（**强约束**）：

* 本模块**只依赖** ``helpers.py``，**禁止**导入 ``importer.py``；
* 以保证"只部署导出能力"的场景不需要拖入导入侧依赖。

输出产物是一份 JSON（:data:`MigrationPayload`），结构与 :func:`AiAgentMetadataExporter._make_payload`
中的定义保持一致；所有含敏感字段都会被 :func:`sanitize_sensitive` 脱敏。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from paasng.platform.applications.ai_agent_migration.helpers import (
    SCHEMA_VERSION,
    TOOL_VERSION,
    AppPayload,
    MetadataValidationError,
    MigrationPayload,
    MigrationReport,
    sanitize_sensitive,
    serialize_model,
)
from paasng.platform.applications.models import Application, ModuleEnvironment

if TYPE_CHECKING:
    from paasng.platform.modules.models import Module

logger = logging.getLogger(__name__)


def export_ai_agent_metadata(
    app_code: str | None = None,
    source_env: str = "",
) -> tuple[MigrationPayload, MigrationReport]:
    """导出单个或全部 AI Agent 应用。

    本函数是**面向 management command 的顶层入口**，仅做分发：``app_code`` 不为空时走
    :meth:`AiAgentMetadataExporter.export_one`；否则走 :meth:`AiAgentMetadataExporter.export_all`。

    :param app_code: 要导出的应用 code。为空时导出全部 AI Agent 应用。
    :param source_env: 源环境的人类可读名字，会被写入 payload 的 ``source_env`` 字段，
        仅用于调试/追溯，不参与任何逻辑判断。
    :return: ``(payload, report)`` 元组。返回后由上层调用方负责落盘与报告输出。
    """
    exporter = AiAgentMetadataExporter(source_env=source_env)
    if app_code:
        return exporter.export_one(app_code)
    return exporter.export_all()


class AiAgentMetadataExporter:
    """AI Agent 应用元数据导出器。

    主要职责：

    1. 从 DB 查出 AI Agent 应用及其关联资源（Module / ModuleEnvironment / BuildConfig / 进程编排 / 部署钩子 / 插件 Profile / 沙箱卷）；
    2. 按预定字段白名单序列化为 JSON 字典；
    3. 对敏感字段脱敏处理；
    4. 生成一份同时包含 payload 与报告的返回值。

    本类**不负责**文件 IO（由 management command 调用 :func:`dump_payload`）、不负责错误输出，
    只返回纯数据，便于被其他入口（如未来的 HTTP API）复用。
    """

    def __init__(self, source_env: str = ""):
        # 优先级：显式传入 > settings.BKPAAS_ENVIRONMENT > settings.DEFAULT_REGION_NAME
        # 都为空时保留空串；source_env 仅为调试这加，不参与逻辑判断。
        self.source_env = (
            source_env or getattr(settings, "BKPAAS_ENVIRONMENT", "") or getattr(settings, "DEFAULT_REGION_NAME", "")
        )

    def export_one(self, app_code: str) -> tuple[MigrationPayload, MigrationReport]:
        """导出单个 AI Agent 应用。

        任何错误（应用不存在 / 不是 AI Agent 应用 / 序列化异常）都会记录到
        ``report.failed`` 后返回原始异常上抛，交由上层决定是否中断整个导出流程。
        """
        report = MigrationReport(total=1)
        try:
            app = self._get_ai_agent_app(app_code)
            payload = self._make_payload(
                [self._serialize_application(app)],
                scope={"type": "single", "app_code": app_code},
            )
        except Exception as e:
            report.failed[app_code] = str(e)
            raise
        else:
            report.succeeded.append(app_code)
            return payload, report

    def export_all(self) -> tuple[MigrationPayload, MigrationReport]:
        """导出当前环境下所有 ``is_ai_agent_app=True`` 的应用。

        与 :meth:`export_one` 不同，本方法对单个应用的序列化异常在记录后**不会上抛**，
        以避免某一个孤立应用的异常阻断整个批量导出流程。失败信息会出现在 ``report.failed`` 中，
        运维同学可以据此决定是否使用本次导出产物。
        """
        apps = Application.objects.filter(is_ai_agent_app=True).order_by("code")
        report = MigrationReport(total=apps.count())
        app_payloads: list[AppPayload] = []
        for app in apps:
            try:
                app_payloads.append(self._serialize_application(app))
            except Exception as e:
                logger.exception("failed to export AI Agent application: %s", app.code)
                report.failed[app.code] = str(e)
            else:
                report.succeeded.append(app.code)

        if not app_payloads:
            report.warnings.append("当前环境没有可导出的 AI Agent 应用。")
        return self._make_payload(app_payloads, scope={"type": "all"}), report

    @staticmethod
    def _get_ai_agent_app(app_code: str) -> Application:
        """按 code 查找应用，并校验是 AI Agent 应用。

        这里主动拒绝非 AI Agent 应用是为了：

        * 避免运维误用本工具跨环境复制任意应用（本工具未覆盖普通应用的所有依赖）；
        * 与导入端的校验逻辑对齐，保持闭环。
        """
        try:
            app = Application.objects.get(code=app_code)
        except Application.DoesNotExist as e:
            raise MetadataValidationError(f"应用不存在: {app_code}") from e

        if not app.is_ai_agent_app:
            raise MetadataValidationError(f"应用不是 AI Agent 应用: {app_code}")
        return app

    def _make_payload(self, applications: list[AppPayload], scope: dict[str, Any]) -> MigrationPayload:
        """组装顶层 payload 结构。

        :param applications: 已序列化的应用列表；可为空。
        :param scope: 本次导出范围描述，例如 ``{"type": "single", "app_code": "foo"}`` 或 ``{"type": "all"}``。
            这个字段仅供人工反查，导入端不会依赖它。
        """
        return {
            "schema_version": SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "source_env": self.source_env,
            "exported_at": timezone.now().isoformat(),
            "scope": scope,
            "applications": applications,
        }

    def _serialize_application(self, app: Application) -> AppPayload:
        """把单个 Application 序列化为包含全部依赖资源的子结构。

        输出顺序与导入端期望保持一致：
        application -> modules（默认模块优先） -> plugin -> agent_sandbox -> uncovered_items。
        """
        # 默认模块放在模块列表首位，避免导入端获取"默认模块"时还要游历。
        modules = [self._serialize_module(module) for module in app.modules.all().order_by("-is_default", "name")]
        return {
            "application": serialize_model(
                app,
                fields=(
                    "id",
                    "code",
                    "name",
                    "name_en",
                    "app_tenant_mode",
                    "app_tenant_id",
                    "type",
                    "is_smart_app",
                    "is_plugin_app",
                    "is_ai_agent_app",
                    "language",
                    "creator",
                    "owner",
                    "region",
                    "is_active",
                    "last_deployed_date",
                    "tenant_id",
                ),
            ),
            "modules": modules,
            "plugin": self._serialize_plugin(app),
            "agent_sandbox": self._serialize_agent_sandbox(app),
            "uncovered_items": self._get_uncovered_items(app),
        }

    def _serialize_module(self, module: Module) -> dict[str, Any]:
        """序列化单个 Module，所有子资源都是"在模块范围内可跨环境重建"的部分。

        运行态资源（部署记录 / 镜像构建记录 / 增强服务实例）不在此处导出。
        """
        return {
            "module": serialize_model(
                module,
                fields=(
                    "id",
                    "name",
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
                ),
            ),
            # 环境顺序以环境名排序（stag 在前、prod 在后，按字典序），减少 diff 噪声。
            "environments": [self._serialize_environment(env) for env in module.envs.all().order_by("environment")],
            "build_config": self._serialize_build_config(module),
            "process_specs": self._serialize_process_specs(module),
            "deploy_hooks": self._serialize_deploy_hooks(module),
        }

    @staticmethod
    def _serialize_environment(env: ModuleEnvironment) -> dict[str, Any]:
        """序列化 ModuleEnvironment 及其关联的 EngineApp。

        这里记录 EngineApp 仅是为了在导入端能检测名称完全一致以避免重复创建，
        并不是完整同步 EngineApp 资源——这个响応在导入端由 ModuleInitializer 重新创建。
        """
        engine_app = env.engine_app
        return {
            "environment": serialize_model(
                env,
                fields=("environment", "is_offlined", "region", "tenant_id"),
            ),
            "engine_app": serialize_model(
                engine_app,
                fields=("id", "name", "region", "is_active", "owner", "tenant_id"),
            ),
        }

    @staticmethod
    def _serialize_build_config(module: Module) -> dict[str, Any] | None:
        """序列化 BuildConfig。模块未创建 BuildConfig 时返回 None。

        Buildpack 与 Builder/Runner 以**名字**引用跨环境同步，原因是它们的主键在不同环境
        不一致，但名字是跨环境稳定的运维约定。名字在目标环境不存在时，导入端会产生 warning。
        """
        try:
            cfg = module.build_config
        except ObjectDoesNotExist:
            return None

        data = serialize_model(
            cfg,
            fields=(
                "build_method",
                "dockerfile_path",
                "docker_build_args",
                "image_repository",
                "image_credential_name",
                "tag_options",
                "use_bk_ci_pipeline",
                "tenant_id",
            ),
        )
        # 以"名字"引用 builder/runner/buildpack：跨环境部署时，名字是运维约定的稳定量，
        # 主键则不是。
        data["buildpack_builder_name"] = cfg.buildpack_builder.name if cfg.buildpack_builder else None
        data["buildpack_runner_name"] = cfg.buildpack_runner.name if cfg.buildpack_runner else None
        data["buildpack_names"] = list(cfg.buildpacks.order_by("name").values_list("name", flat=True))
        # ``image_credential_name`` 本身含 key/credential 的词根，会被占位符脱敏；
        # 这里不是问题，导入端检测到占位符会跳过写入，运维需手动补。
        return sanitize_sensitive(data)

    @staticmethod
    def _serialize_process_specs(module: Module) -> list[dict[str, Any]]:
        """序列化 ``ModuleProcessSpec`` 与其 env_overlays，保证跨环境进程编排一致。

        每个 spec 含主体字段与 env_overlays 列表；主体字段会走 sanitize_sensitive，
        env_overlays 不含敏感字段所以不需要。
        """
        specs = []
        for spec in module.process_specs.all().order_by("name"):
            data = serialize_model(
                spec,
                fields=(
                    "name",
                    "proc_command",
                    "command",
                    "args",
                    "port",
                    "services",
                    "target_replicas",
                    "plan_name",
                    "autoscaling",
                    "scaling_config",
                    "probes",
                    "graceful_shutdown_seconds",
                    "components",
                    "tenant_id",
                ),
            )
            data["env_overlays"] = [
                serialize_model(
                    overlay,
                    fields=(
                        "environment_name",
                        "override_plan_name",
                        "override_resources",
                        "target_replicas",
                        "plan_name",
                        "autoscaling",
                        "scaling_config",
                        "tenant_id",
                    ),
                )
                for overlay in spec.env_overlays.all().order_by("environment_name")
            ]
            specs.append(sanitize_sensitive(data))
        return specs

    @staticmethod
    def _serialize_deploy_hooks(module: Module) -> list[dict[str, Any]]:
        """序列化部署钩子。按 ``type`` 排序以保持 diff 稳定。"""
        return [
            sanitize_sensitive(
                serialize_model(
                    hook,
                    fields=("type", "proc_command", "command", "args", "enabled", "tenant_id"),
                )
            )
            for hook in module.deploy_hooks.all().order_by("type")
        ]

    @staticmethod
    def _serialize_plugin(app: Application) -> dict[str, Any]:
        """序列化插件相关元数据（仅针对 ``is_plugin_app=True`` 的应用才会有内容）。

        包含三部分：

        * ``profile``：插件介绍 / 分类 / API网关引用等。在插件应用但 profile 缺失时，记录 ``missing_profile=True``。
        * ``distributor_code_names``：插件允许使用方名字列表。
        * ``tag_code_name``：插件分类标签名字。

        API网关实体本身（位于 BkApiGateway 服务）不在本工具迁移范围内，
        只部分 ``api_gw_id`` / ``api_gw_name`` 引用。
        """
        result: dict[str, Any] = {"profile": None, "distributor_code_names": []}
        try:
            profile = app.bk_plugin_profile
        except ObjectDoesNotExist:
            if app.is_plugin_app:
                # 插件应用本该有 profile，缺失为异常场景，留个标记供运维人工介入。
                result["missing_profile"] = True
            return result

        profile_data = serialize_model(
            profile,
            fields=(
                "introduction",
                "contact",
                "api_gw_name",
                "api_gw_id",
                "api_gw_last_synced_at",
                "pre_distributor_codes",
                "owner",
                "region",
                "tenant_id",
            ),
        )
        profile_data["tag_code_name"] = profile.tag.code_name if profile.tag else None
        result["profile"] = sanitize_sensitive(profile_data)
        result["distributor_code_names"] = list(
            app.distributors.order_by("code_name").values_list("code_name", flat=True)
        )
        return result

    @staticmethod
    def _serialize_agent_sandbox(app: Application) -> dict[str, Any]:
        """序列化 AI Agent 沙箱卷。

        只包含卷的元数据（name / display_name / deleted_at / tenant_id）；
        卷底层的 CFS 文件内容不在 payload 中，需要运维从源环境按
        ``app/{uuid_hex}`` 路径单独同步到目标环境。
        """
        volumes = [
            serialize_model(volume, fields=("name", "display_name", "deleted_at", "tenant_id"))
            for volume in app.agent_sandbox_volumes.all().order_by("name")
        ]
        return {"volumes": volumes}

    @staticmethod
    def _get_uncovered_items(app: Application) -> list[str]:
        """返回"本次导出未覆盖项"说明列表，写入 payload 供运维手动补齐。

        这些项起过**扫雷作用**：提醒导入人员哪些资源不在本工具范围内、需要另外手工处理。
        与 DESIGN.md 中的"未覆盖表"保持一致，修改时两者需同步。
        """
        items = [
            "不会导出 OAuth 客户端密钥、镜像仓库密码、增强服务实例凭据、运行态部署记录和沙箱 daemon_token。",
            "不会导出 Sandbox 运行实例、ImageBuildRecord、ImageBuildLog 以及底层镜像构建产物。",
            "插件 API Gateway 外部资源只导出 api_gw_id/api_gw_name 引用，不会创建或同步网关实体。",
        ]
        if app.agent_sandbox_volumes.exists():
            items.append("沙箱共享卷只导出卷元数据，不导出底层 CFS 文件内容。")
        return items
