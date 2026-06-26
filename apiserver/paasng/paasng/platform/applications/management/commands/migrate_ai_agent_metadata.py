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

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from paasng.platform.applications.ai_agent_migration import (
    ImportOptions,
    dump_payload,
    export_ai_agent_metadata,
    import_ai_agent_metadata,
    load_payload,
    preflight_ai_agent_metadata,
)


class Command(BaseCommand):
    help = "Export, preflight or import AI Agent application metadata."

    def add_arguments(self, parser):
        subparsers = parser.add_subparsers(dest="action", required=True)

        export_parser = subparsers.add_parser("export", help="Export AI Agent application metadata")
        export_parser.add_argument("--app_code", dest="app_code", type=str, help="APP ID to export")
        export_parser.add_argument(
            "--all", dest="export_all", action="store_true", help="Export all AI Agent applications"
        )
        export_parser.add_argument("--output", dest="output", type=str, required=True, help="Output JSON file path")
        export_parser.add_argument("--source_env", dest="source_env", type=str, default="", help="Source env label")

        preflight_parser = subparsers.add_parser("preflight", help="Preflight AI Agent metadata import")
        self._add_import_arguments(preflight_parser, dry_run_default=True)

        import_parser = subparsers.add_parser("import", help="Import AI Agent application metadata")
        self._add_import_arguments(import_parser, dry_run_default=False)

    @staticmethod
    def _add_import_arguments(parser, dry_run_default: bool):
        parser.add_argument("--input", dest="input", type=str, required=True, help="Input JSON file path")
        parser.add_argument(
            "--conflict_strategy",
            dest="conflict_strategy",
            choices=["fail", "skip", "update"],
            default="fail",
            help="Strategy when target object exists",
        )
        parser.add_argument("--operator", dest="operator", type=str, default=None, help="Operator user id")
        parser.add_argument(
            "--env_cluster_mapping",
            dest="env_cluster_mapping",
            type=str,
            default="{}",
            help='JSON object for environment cluster mapping, e.g. {"stag":"default", "prod":"prod"}',
        )
        parser.add_argument(
            "--region_mapping",
            dest="region_mapping",
            type=str,
            default="{}",
            help='JSON object for region mapping, e.g. {"ieod":"default"}',
        )
        parser.add_argument(
            "--root_domain_mapping",
            dest="root_domain_mapping",
            type=str,
            default="{}",
            help="JSON object for module preferred root domain mapping",
        )
        parser.add_argument(
            "--mapping_file",
            dest="mapping_file",
            type=str,
            default=None,
            help="Optional JSON file containing env_cluster_mapping, region_mapping and root_domain_mapping",
        )
        parser.add_argument(
            "--send_create_signal",
            dest="send_create_signal",
            action="store_true",
            help="Send post_create_application signal after creating a new application",
        )
        parser.set_defaults(dry_run=dry_run_default)

    def handle(self, action: str, *args, **options):
        if action == "export":
            self._handle_export(options)
            return
        if action == "preflight":
            self._handle_preflight(options)
            return
        if action == "import":
            self._handle_import(options)
            return
        raise CommandError(f"Unsupported action: {action}")

    def _handle_export(self, options: dict[str, Any]) -> None:
        app_code = options.get("app_code")
        export_all = options.get("export_all")
        if bool(app_code) == bool(export_all):
            raise CommandError("Please specify exactly one of --app_code or --all.")

        payload, report = export_ai_agent_metadata(app_code=app_code, source_env=options.get("source_env") or "")
        dump_payload(payload, options["output"])
        self.stdout.write(self.style.SUCCESS(f"Exported metadata to {Path(options['output']).resolve()}"))
        self.stdout.write(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))

    def _handle_preflight(self, options: dict[str, Any]) -> None:
        payload = load_payload(options["input"])
        report = preflight_ai_agent_metadata(payload, options=self._make_import_options(options, dry_run=True))
        self.stdout.write(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        if report.has_blocking_conflicts:
            raise CommandError("Preflight found blocking conflicts.")

    def _handle_import(self, options: dict[str, Any]) -> None:
        payload = load_payload(options["input"])
        report = import_ai_agent_metadata(payload, options=self._make_import_options(options, dry_run=False))
        self.stdout.write(json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
        if report.failed:
            raise CommandError("Import finished with failures.")
        if report.has_blocking_conflicts:
            raise CommandError("Import blocked by conflicts.")
        self.stdout.write(self.style.SUCCESS("Import finished."))

    def _make_import_options(self, options: dict[str, Any], dry_run: bool) -> ImportOptions:
        mapping_config = self._load_mapping_file(options.get("mapping_file"))
        return ImportOptions(
            conflict_strategy=options["conflict_strategy"],
            operator=options.get("operator"),
            env_cluster_mapping={
                **mapping_config.get("env_cluster_mapping", {}),
                **self._load_mapping(options.get("env_cluster_mapping") or "{}"),
            },
            region_mapping={
                **mapping_config.get("region_mapping", {}),
                **self._load_mapping(options.get("region_mapping") or "{}"),
            },
            root_domain_mapping={
                **mapping_config.get("root_domain_mapping", {}),
                **self._load_mapping(options.get("root_domain_mapping") or "{}"),
            },
            dry_run=dry_run,
            send_create_signal=bool(options.get("send_create_signal")),
        )

    @classmethod
    def _load_mapping_file(cls, path: str | None) -> dict[str, dict[str, str]]:
        if not path:
            return {}
        try:
            with Path(path).open(encoding="utf-8") as fp:
                data = json.load(fp)
        except json.JSONDecodeError as e:
            raise CommandError(f"Invalid mapping file JSON: {path}") from e
        if not isinstance(data, dict):
            raise CommandError("Mapping file must be a JSON object.")

        result = {}
        for key in ("env_cluster_mapping", "region_mapping", "root_domain_mapping"):
            value = data.get(key, {})
            if not isinstance(value, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in value.items()
            ):
                raise CommandError(f"Mapping file field {key} must be an object whose keys and values are strings.")
            result[key] = value
        return result

    @staticmethod
    def _load_mapping(value: str) -> dict[str, str]:
        try:
            data = json.loads(value)
        except json.JSONDecodeError as e:
            raise CommandError(f"Invalid mapping JSON: {value}") from e
        if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
            raise CommandError("Mapping argument must be a JSON object whose keys and values are strings.")
        return data
