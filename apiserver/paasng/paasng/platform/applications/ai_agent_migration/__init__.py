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

"""AI Agent application metadata migration package.

本包负责在不同 BkPaaS 部署环境之间迁移 AI Agent 应用的元数据，被唯一的
Django management command ``migrate_ai_agent_metadata`` 调用。该命令以子命令形式提供
三个动作：``export`` / ``preflight`` / ``import``。

拆包原因与架构约束（**强约束**，代码审议时请保持）：

* ``helpers.py``  ——常量、报告与选项数据类、JSON IO、序列化与脱敏工具；必须保持不依赖
  另外两个模块，以便"只部署导出能力"或"只部署导入能力"的场景只需携带单侧逻辑。
* ``exporter.py`` ——导出侧逻辑，**仅依赖** ``helpers.py``，**禁止**导入 ``importer.py``。
* ``importer.py`` ——导入与预检逻辑，**仅依赖** ``helpers.py``，**禁止**导入 ``exporter.py``。

包根下重新导出最常用的符号，便于上层以
``from paasng.platform.applications.ai_agent_migration import ...`` 一行引入。

运维使用详见同目录的 ``OPERATION.md``；设计背景与未覆盖项详见同目录的 ``DESIGN.md``。
"""

from paasng.platform.applications.ai_agent_migration.exporter import (
    AiAgentMetadataExporter,
    export_ai_agent_metadata,
)
from paasng.platform.applications.ai_agent_migration.helpers import (
    SCHEMA_VERSION,
    SENSITIVE_PLACEHOLDER,
    TOOL_VERSION,
    AppPayload,
    ImportOptions,
    MetadataValidationError,
    MigrationPayload,
    MigrationReport,
    ObjectAction,
    dump_payload,
    load_payload,
)
from paasng.platform.applications.ai_agent_migration.importer import (
    AiAgentMetadataImporter,
    import_ai_agent_metadata,
    preflight_ai_agent_metadata,
)

__all__ = [
    "SCHEMA_VERSION",
    "SENSITIVE_PLACEHOLDER",
    "TOOL_VERSION",
    "AiAgentMetadataExporter",
    "AiAgentMetadataImporter",
    "AppPayload",
    "ImportOptions",
    "MetadataValidationError",
    "MigrationPayload",
    "MigrationReport",
    "ObjectAction",
    "dump_payload",
    "export_ai_agent_metadata",
    "import_ai_agent_metadata",
    "load_payload",
    "preflight_ai_agent_metadata",
]
