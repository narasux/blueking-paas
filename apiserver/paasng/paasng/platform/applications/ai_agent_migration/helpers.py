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

"""Shared helpers for AI Agent application metadata migration.

本模块承载导出端（exporter）与导入端（importer）共同需要的逻辑。

架构约束（**强约束**）：

* 本模块**禁止** ``import exporter.py`` 或 ``importer.py``，以保持单向依赖；
* 之所以这样拆，是为了让"只部署导出能力"或"只部署导入能力"的场景，
  只需要带上 ``helpers.py`` + 对应一侧的模块即可，避免拖入另一侧无关依赖；
* 任何被双方共享的常量、dataclass、纯函数都集中放在这里。

模块内主要分四层：

1. 常量与字段白名单（``SCHEMA_VERSION`` / ``APP_UPDATE_FIELDS`` 等）；
2. 报告与选项数据类（``ObjectAction`` / ``MigrationReport`` / ``ImportOptions``）；
3. JSON 文件 IO（``load_payload`` / ``dump_payload``）；
4. 模型序列化与字段比对（``serialize_model`` / ``jsonify`` / ``diff_dicts`` 等）。
"""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal

import cattr
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# 导出文件的 schema 版本号。导入端会校验 payload["schema_version"] 必须与本值相等，
# 不一致则直接抛 ``MetadataValidationError``。
# 后续若 payload 结构发生**不向后兼容**的变更（如字段重命名、删除、语义变化），
# 必须在此处升版（例如 "1" -> "2"），并在 importer 端补充对旧版本的兼容/迁移逻辑。
SCHEMA_VERSION = "1"

# 工具版本号。仅用于在 payload 中留痕、便于排查问题，不参与任何逻辑判断。
# 通常约定为发布日期；每次发布对应工具改动时手动更新。
TOOL_VERSION = "2026.06.23"

# 导入端支持的冲突处理策略：
#   - "fail"：目标已存在且字段有差异，则拒绝导入；
#   - "skip"：目标已存在则保留原值，跳过该应用；
#   - "update"：目标已存在则用 payload 中的值覆盖差异字段。
SUPPORTED_CONFLICT_STRATEGIES = {"fail", "skip", "update"}

# 敏感字段的占位符。导出时凡是命中 ``SENSITIVE_KEYWORDS`` 的字段都会被替换为本值，
# 导入时遇到该值则**跳过**对应字段的写入，避免把占位符当成真实数据写回 DB。
# 取值刻意写得足够长且业务里不会出现，以确保不会与真实数据冲突。
SENSITIVE_PLACEHOLDER = "__BKPAAS_AI_AGENT_MIGRATION_SENSITIVE_VALUE__"

# 敏感字段的关键字白名单。``sanitize_sensitive`` 会以**子串包含**的方式匹配（不区分大小写），
# 命中则把字段值置换为 ``SENSITIVE_PLACEHOLDER``。
# 增删此处时务必同步评估：
#   1. 新增关键字会不会误伤业务字段；
#   2. 删除关键字会不会让本不应导出的密钥泄漏到 JSON。
SENSITIVE_KEYWORDS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "key",
    "private_key",
    "credential",
    "authorization",
    "auth",
)

# ``Application`` 表中可被本工具同步的字段白名单。
# 故意排除掉 ``id`` / ``code`` / 时间戳 / 关联外键等不应跨环境复制的字段，
# 以及 ``logo`` 这类二进制资源。
# 字段顺序仅影响 diff 报告的展示顺序，不影响功能。
APP_UPDATE_FIELDS = (
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
)

# ``Module`` 表中可被本工具同步的字段白名单（同上原则）。
# 注意 ``name`` 不在此处——模块以 ``(application, name)`` 复合定位，
# 不允许通过 update 改名（会导致环境间引用错乱）。
MODULE_UPDATE_FIELDS = (
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

# 类型别名，纯粹为了让 exporter/importer 的函数签名更具自描述性。
# ``AppPayload``：单个应用在 payload 中的子结构；
# ``MigrationPayload``：整份导出文件的顶层结构。
AppPayload = dict[str, Any]
MigrationPayload = dict[str, Any]
ConflictStrategy = Literal["fail", "skip", "update"]


# ---------------------------------------------------------------------------
# Report and option types
# ---------------------------------------------------------------------------


@dataclass
class ObjectAction:
    """描述一次"被规划或已执行"的迁移动作。

    在 preflight 阶段产出动作清单（不写库），在 import 阶段则记录实际产生的变更。

    :param action: 动作类型，可选值：``create`` / ``update`` / ``conflict`` / ``skip``。
        ``conflict`` 仅出现在 preflight 报告或 conflict_strategy="fail" 时。
    :param object_type: 对象类型字符串，如 "Application" / "Module"，用于报告归类。
    :param object_id: 对象的人类可读 ID。Application 用 app_code，Module 用 "app_code:module_name"。
    :param reason: 简短原因，例如"目标环境不存在应用"。便于运维直接读取。
    :param details: 详情字典，目前主要存放 ``{"diffs": {...}}``。结构未来可扩展。
    """

    action: str
    object_type: str
    object_id: str
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """序列化为 JSON 友好字典，供 management command stdout 输出。"""
        return dataclasses.asdict(self)


@dataclass
class MigrationReport:
    """导出 / 预检 / 导入操作的统一报告对象。

    本 dataclass 同时被 export / preflight / import 三个动作复用，
    各字段的语义在不同动作下略有差异：

    * **export** 时：
        - ``total`` 期望导出的应用数；
        - ``succeeded`` 已成功序列化的 app_code；
        - ``failed`` 序列化失败的 app_code -> 错误消息；
        - ``warnings`` 记录"无可导出应用"等提醒。
    * **preflight** 时：
        - ``created`` / ``updated`` / ``conflicts`` / ``skipped`` 表示"将会发生"的动作；
        - 不会写库。
    * **import** 时：
        - ``succeeded`` / ``failed`` 记录已实际处理的应用结果；
        - ``created`` / ``updated`` 记录实际产生的变更；
        - 由于按 application 粒度走 ``transaction.atomic()``，单应用失败不影响其他应用。

    :param total: 本次操作涉及的应用总数。
    :param succeeded: 操作成功的 app_code 列表。
    :param failed: 操作失败的 ``{app_code: 错误消息}`` 字典。
    :param skipped: 被跳过的 app_code（含 conflict_strategy="skip" 与无差异跳过两种情况）。
    :param created: 创建动作明细列表，元素为 :class:`ObjectAction`。
    :param updated: 更新动作明细列表。
    :param conflicts: 冲突动作明细列表（仅在 fail 策略或 preflight 时填充）。
    :param warnings: 警告消息列表，例如缺少集群映射、引用了不存在的 buildpack 等。
    """

    total: int = 0
    succeeded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    created: list[ObjectAction] = field(default_factory=list)
    updated: list[ObjectAction] = field(default_factory=list)
    conflicts: list[ObjectAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_action(self, action: ObjectAction) -> None:
        """按 ``action.action`` 类型把动作分流到对应桶。

        skip 类型只把 object_id 加进 ``skipped``，不保留完整 ObjectAction，
        因为 skip 不需要展示差异详情。
        """
        if action.action == "create":
            self.created.append(action)
        elif action.action == "update":
            self.updated.append(action)
        elif action.action == "conflict":
            self.conflicts.append(action)
        elif action.action == "skip":
            self.skipped.append(action.object_id)

    def as_dict(self) -> dict[str, Any]:
        """序列化为 JSON 友好字典，供 management command 输出与持久化。"""
        return {
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "skipped": self.skipped,
            "created": [item.as_dict() for item in self.created],
            "updated": [item.as_dict() for item in self.updated],
            "conflicts": [item.as_dict() for item in self.conflicts],
            "warnings": self.warnings,
        }

    @property
    def has_blocking_conflicts(self) -> bool:
        """是否存在阻塞性冲突（用于 import 阶段决定是否中止）。"""
        return bool(self.conflicts)


@dataclass
class ImportOptions:
    """导入操作的可调参数集合。

    :param conflict_strategy: 目标环境已有同 code 应用时的处理策略，详见
        :data:`SUPPORTED_CONFLICT_STRATEGIES`。默认 ``fail``，即只允许导入"全新"应用，
        以避免无意中覆盖目标环境数据。
    :param operator: 创建应用时使用的 operator 用户 ID。为空时回退使用
        payload 中的 ``creator`` / ``owner``，再回退使用 ``"admin"``。
    :param env_cluster_mapping: ``{环境名: 集群名}`` 映射。例如 ``{"stag": "default"}``。
        未配置的环境会让 ``ModuleInitializer`` 走目标环境**默认分配策略**，
        并在 preflight 中产出 warning。
    :param region_mapping: ``{源 region: 目标 region}`` 映射。导入时会对应用与模块的
        ``region`` 字段做翻译。常见用途：``{"ieod": "default"}``。
    :param root_domain_mapping: ``{源根域: 目标根域}`` 映射。导入时翻译模块的
        ``user_preferred_root_domain`` 字段；仅当源值命中映射时才替换。
    :param dry_run: 是否仅做预检（True 时等价 preflight，所有写库操作都被跳过）。
        通常通过 ``preflight_ai_agent_metadata`` 入口而非手动设置。
    :param send_create_signal: 创建新应用后是否发送 ``post_create_application`` 信号。
        默认 False，避免触发 IAM / OAuth 等下游初始化逻辑（这些通常已由
        ``create_application()`` 内部一次性完成）。仅当目标环境配置了自定义
        post_create_application 接收器需要被触发时才开启。
    """

    conflict_strategy: ConflictStrategy = "fail"
    operator: str | None = None
    env_cluster_mapping: dict[str, str] = field(default_factory=dict)
    region_mapping: dict[str, str] = field(default_factory=dict)
    root_domain_mapping: dict[str, str] = field(default_factory=dict)
    dry_run: bool = False
    send_create_signal: bool = False

    def __post_init__(self) -> None:
        # 校验冲突策略合法性。dataclass 字段类型 ``ConflictStrategy`` 是 Literal，
        # 但运行时不强制，所以仍需手动校验，防止外部传入非法值。
        if self.conflict_strategy not in SUPPORTED_CONFLICT_STRATEGIES:
            raise ValueError(f"unsupported conflict strategy: {self.conflict_strategy}")


class MetadataValidationError(ValueError):
    """导入文件结构非法 / 不满足业务前置条件时抛出。

    继承自 ``ValueError`` 而非 ``Exception``，以便上层既可以按 ``ValueError`` 通用捕获，
    也可以专门捕获本类型做精细化处理。
    """


# ---------------------------------------------------------------------------
# JSON file IO
# ---------------------------------------------------------------------------


def load_payload(path: str | Path) -> MigrationPayload:
    """从单一 UTF-8 JSON 文件加载迁移 payload。

    本工具刻意只支持"单一文件"——便于 ``kubectl cp`` 跨容器传输与 diff。
    """
    with Path(path).open(encoding="utf-8") as fp:
        return json.load(fp)


def dump_payload(payload: MigrationPayload, path: str | Path) -> None:
    """把迁移 payload 写入单一 UTF-8 JSON 文件。

    文件总是以 ``indent=2`` + ``ensure_ascii=False`` + ``sort_keys=True`` 序列化，目的：

    * ``indent=2``：人工可读，方便运维直接 ``cat`` 检查；
    * ``ensure_ascii=False``：保留中文应用名 / 描述等原文，避免 ``\\uXXXX`` 转义；
    * ``sort_keys=True``：保证两次导出对同样输入产出**字节级一致**的文件，便于 git diff。

    末尾会追加一个换行符，符合常见 Unix 文本文件惯例（避免 ``no newline at end of file``）。
    """
    with Path(path).open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=True)
        fp.write("\n")


# ---------------------------------------------------------------------------
# Model serialization & sanitization
# ---------------------------------------------------------------------------


def serialize_model(instance: Any, fields: Iterable[str]) -> dict[str, Any]:
    """把 Django 模型实例的指定字段序列化为 JSON 兼容字典。

    ``django.forms.models.model_to_dict`` 默认只导出可编辑字段，对于自动主键
    （``editable=False``）、被自定义 ``__init__`` 初始化的字段会缺失，所以这里在
    ``model_to_dict`` 的基础上再做一次"按字段名兜底取值"，保证白名单内每个字段都被采到。

    :param instance: 任意 Django model 实例。
    :param fields: 想导出的字段名集合。**必须是字符串可迭代对象**，由调用方维护。
    :return: 已经过 :func:`jsonify` 的 JSON 兼容字典。
    """
    # 局部导入而非顶层导入：让本 helpers 模块在没有 Django 的代码评审环境
    # （如纯静态分析容器）里也能被加载，便于跨环境 lint。
    from django.forms.models import model_to_dict

    data = model_to_dict(instance, fields=fields)
    for field_name in fields:
        # ``model_to_dict`` 跳过的字段（典型如自动主键 id），用 getattr 兜底。
        if field_name not in data and hasattr(instance, field_name):
            data[field_name] = getattr(instance, field_name)
    return jsonify(data)


def jsonify(value: Any) -> Any:  # noqa: PLR0911
    """递归把任意 Python 值转换成 JSON 可序列化的原始类型。

    转换规则按优先级（从上到下）：

    1. ``None`` / ``str`` / ``int`` / ``float`` / ``bool``：原样返回；
    2. ``Decimal``：转为字符串，避免精度丢失；
    3. ``UUID``：转为 32 位 hex 字符串（与 BkPaaS 中 UUIDField 默认表示一致）；
    4. ``datetime`` / ``date``：转为 ISO 8601 字符串；
    5. ``Enum``：取 ``.value``；
    6. dataclass / pydantic ``BaseModel``：递归转 dict（兼容 v1 ``dict()`` 与 v2 ``model_dump()``）；
    7. dict：递归处理 key 与 value（key 强转为 str）；
    8. list / tuple / set：递归转列表；
    9. 兜底使用 ``cattr.unstructure``；再失败则强转字符串。

    设计要点：

    * 该函数是"宽容型"——绝不抛异常，最坏也是 ``str(value)``，保证序列化流程不会中断；
    * 输出可直接喂给 ``json.dumps`` 而无需自定义 encoder。
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, uuid.UUID):
        return value.hex
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value):
        return jsonify(dataclasses.asdict(value))
    if isinstance(value, BaseModel):
        # 兼容 pydantic v2（model_dump）与 v1（dict）。
        if hasattr(value, "model_dump"):
            return jsonify(value.model_dump())
        return jsonify(value.dict())
    if isinstance(value, dict):
        # JSON 规范要求 key 必须是字符串；这里强制 str 化以避免后续 ``json.dumps`` 失败。
        return {str(jsonify(k)): jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonify(item) for item in value]
    try:
        # 兜底：用 cattr 把任意附加类型解构成基础容器后再递归。
        return jsonify(cattr.unstructure(value))
    except Exception:  # noqa: BLE001
        # 最终兜底：强转字符串，宁可丢类型也不能让导出整体失败。
        return str(value)


def sanitize_sensitive(value: Any, path: str = "") -> Any:
    """递归把命中敏感关键字的字段值替换为 :data:`SENSITIVE_PLACEHOLDER`。

    匹配规则：

    * 仅对 dict 的 **key** 名称做关键字匹配（不区分大小写、子串包含），见 :func:`is_sensitive_key`；
    * 对 list / 嵌套 dict 递归下钻；
    * **空值**（None / "" / [] / {}）即使 key 命中也保持原样，避免无意义占位符。

    :param value: 任意 jsonify 后的值。
    :param path: 当前路径，仅用于未来调试日志（目前未使用）。
    """
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            if is_sensitive_key(str(key)) and item not in (None, "", [], {}):
                # 命中敏感 key 且值非空 → 替换为占位符；不再向下递归。
                result[key] = SENSITIVE_PLACEHOLDER
            else:
                result[key] = sanitize_sensitive(item, key_path)
        return result
    if isinstance(value, list):
        return [sanitize_sensitive(item, path) for item in value]
    return value


def is_sensitive_key(key: str) -> bool:
    """判断字段名是否包含敏感关键字（不区分大小写，子串匹配）。"""
    key_lower = key.lower()
    return any(keyword in key_lower for keyword in SENSITIVE_KEYWORDS)


def is_sensitive_placeholder(value: Any) -> bool:
    """判断值或其任一深层子值是否为敏感占位符。

    导入端用它来跳过"占位符值"——一旦发现字段值还是占位符，说明导出时被脱敏了，
    导入时不能用占位符覆盖目标 DB 中的真实凭据，必须保持目标值不变。
    """
    if value == SENSITIVE_PLACEHOLDER:
        return True
    if isinstance(value, dict):
        return any(is_sensitive_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(is_sensitive_placeholder(item) for item in value)
    return False


# ---------------------------------------------------------------------------
# Value parsing & dict diff
# ---------------------------------------------------------------------------


def parse_value(value: Any) -> Any:
    """尽力把 JSON 基础类型还原成 Python 富类型，目前主要还原 ISO 字符串 → datetime。

    设计要点：

    * **best-effort**：不能识别的字符串原样返回，绝不抛异常；
    * 仅识别 ISO 8601 完整 datetime 字符串，date 字符串当前会原样返回
      （``datetime.fromisoformat`` 不接受单纯 date 字符串）；
    * 对 list / dict 递归下钻，便于导入端把整个 payload"局部反序列化"再写回 ORM。
    """
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, list):
        return [parse_value(item) for item in value]
    if isinstance(value, dict):
        return {key: parse_value(item) for key, item in value.items()}
    return value


def diff_dicts(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """按 ``expected`` 的 key 集合做"期望 vs 实际"字段级 diff。

    注意是**单向 diff**——只比较 expected 中存在的 key，actual 多出来的 key 会被忽略。
    这是有意为之：本工具只关心 payload 中声明的字段是否被正确同步到目标，
    目标库里多出来的旁支字段不在迁移工具关心范围内。

    :return: ``{字段名: {"expected": 值, "actual": 值}}``，仅包含**真正不一致**的字段。
    """
    diffs: dict[str, dict[str, Any]] = {}
    for key, value in expected.items():
        # 双方都过一遍 jsonify，排除 datetime/UUID 等导致的"形态差异"。
        actual_value = jsonify(actual.get(key))
        expected_value = jsonify(value)
        if expected_value != actual_value:
            diffs[key] = {"expected": expected_value, "actual": actual_value}
    return diffs


def get_app_code(app_payload: AppPayload) -> str:
    """安全提取 ``app_payload["application"]["code"]``，缺失时返回空串。"""
    return app_payload.get("application", {}).get("code", "")


def extract_app_update_fields(app_data: dict[str, Any]) -> dict[str, Any]:
    """从 payload 的 application 子结构中按 :data:`APP_UPDATE_FIELDS` 白名单截取字段。"""
    return {field_name: app_data[field_name] for field_name in APP_UPDATE_FIELDS if field_name in app_data}


def read_app_update_fields(app: Any) -> dict[str, Any]:
    """从已存在的 ``Application`` 实例上按白名单读取字段（用于 diff 比对的"actual"侧）。"""
    return {field_name: jsonify(getattr(app, field_name)) for field_name in APP_UPDATE_FIELDS}


def extract_module_update_fields(module_data: dict[str, Any]) -> dict[str, Any]:
    """从 payload 的 module 子结构中按 :data:`MODULE_UPDATE_FIELDS` 白名单截取字段。"""
    return {field_name: module_data[field_name] for field_name in MODULE_UPDATE_FIELDS if field_name in module_data}


def read_module_update_fields(module: Any) -> dict[str, Any]:
    """从已存在的 ``Module`` 实例上按白名单读取字段（用于 diff 比对的"actual"侧）。"""
    return {field_name: jsonify(getattr(module, field_name)) for field_name in MODULE_UPDATE_FIELDS}


# ---------------------------------------------------------------------------
# Misc helpers used by importer
# ---------------------------------------------------------------------------


def get_optional_by_name(model_cls: type, name: str | None) -> Any:
    """按 ``name`` 字段查找对象，找不到返回 None；``name`` 为空时直接返回 None。

    主要用于在导入时按名字解析跨表引用（如 buildpack / builder / runner），
    若引用不存在则置空，让对应字段保持 NULL 而不是抛异常。
    """
    if not name:
        return None
    return model_cls.objects.filter(name=name).first()  # type: ignore


def get_wl_app_type(app: Any):
    """将 ``Application.type`` 翻译为 workloads 侧的 :class:`WlAppType`。

    背景：BkPaaS 的 ``Application.type``（位于 ``paasng``）与 workloads 侧的
    ``WlAppType``（位于 ``paas_wl``）是两个独立枚举，需要本函数桥接。

    本函数刻意做**惰性导入**——避免只用 helpers 时把 ``paas_wl`` 整个拖进来，
    保持 helpers 的导入图尽量纯净。
    """
    from paas_wl.bk_app.applications.constants import WlAppType
    from paasng.platform.applications.constants import ApplicationType

    if app.type == ApplicationType.CLOUD_NATIVE.value:
        return WlAppType.CLOUD_NATIVE
    return WlAppType.DEFAULT
