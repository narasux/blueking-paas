# -*- coding: utf-8 -*-
"""
TencentBlueKing is pleased to support the open source community by making
蓝鲸智云 - PaaS 平台 (BlueKing - PaaS System) available.
Copyright (C) 2017 THL A29 Limited, a Tencent company. All rights reserved.
Licensed under the MIT License (the "License"); you may not use this file except
in compliance with the License. You may obtain a copy of the License at

    http://opensource.org/licenses/MIT

Unless required by applicable law or agreed to in writing, software distributed under
the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
either express or implied. See the License for the specific language governing permissions and
limitations under the License.

We undertake not to change the open source license (MIT license) applicable
to the current version of the project delivered to anyone in the future.
"""
import json

from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from paasng.accessories.iam.permissions.resources.application import AppAction
from paasng.accounts.permissions.application import application_perm_class
from paasng.engine.display_blocks import ServicesInfo
from paasng.platform.applications.mixins import ApplicationCodeInPathMixin

from .constants import BKPAAS_ADDONS_ANNO_KEY


class CNativeAppManifestExtViewset(viewsets.ViewSet, ApplicationCodeInPathMixin):
    """云原生应用扩展信息管理"""

    permission_classes = [IsAuthenticated, application_perm_class(AppAction.VIEW_BASIC_INFO)]

    def retrieve(self, request, code, module_name, environment):
        """提供应用扩展信息，主要来源为平台扩展功能，如增强服务配置等"""
        engine_app = self.get_engine_app_via_path()
        service_info = ServicesInfo.get_detail(engine_app)['services_info']
        manifest_ext = {
            "metadata": {
                "annotations": {BKPAAS_ADDONS_ANNO_KEY: json.dumps([addons["name"] for addons in service_info])}
            }
        }
        return Response(data=manifest_ext)
