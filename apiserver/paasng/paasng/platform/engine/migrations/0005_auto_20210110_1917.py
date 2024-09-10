# -*- coding: utf-8 -*-
# TencentBlueKing is pleased to support the open source community by making
# 蓝鲸智云 - PaaS 平台 (BlueKing - PaaS System) available.
# Copyright (C) 2017 THL A29 Limited, a Tencent company. All rights reserved.
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

# Generated by Django 2.2.17 on 2020-12-31 05:20

from django.db import migrations

from paasng.platform.engine.models import DeployPhaseTypes


def forwards_func(apps, schema_editor):
    DeployStepMeta = apps.get_model("engine", "DeployStepMeta")

    default_preparation_step_names = (
        DeployPhaseTypes.PREPARATION.value,
        [
            "解析应用进程信息",
            "上传仓库代码",
            "配置资源实例",
        ],
    )
    default_build_step_names = (
        DeployPhaseTypes.BUILD.value,
        [
            # 下一步的开始就是上一步的成功标志
            ("下载代码", 'Downloading app source code', 'Restoring cache...'),
            ("加载缓存", 'Restoring cache...', '-----> Compiling app...'),
            ("构建应用", '-----> Compiling app...', '-----> Discovering process types'),
            ("检测进程类型", '-----> Discovering process types', '-----> Compiled slug size is'),
            ("制作打包构件", '-----> Compiled slug size is', 'Checking for changes inside the cache directory...'),
            ("上传缓存", 'Checking for changes inside the cache directory...', 'Done: Uploaded cache'),
        ],
    )
    default_release_step_names = (
        DeployPhaseTypes.RELEASE.value,
        [
            "部署应用",
            "检测部署结果",
        ],
    )

    metas = []
    for names in [default_preparation_step_names, default_build_step_names, default_release_step_names]:
        for x in names[1]:
            if isinstance(x, tuple):
                create_params = dict(name=x[0], phase=names[0], started_patterns=[x[1]], finished_patterns=[x[2]])
            else:
                create_params = dict(name=x, phase=names[0])
            metas.append(DeployStepMeta.objects.create(**create_params))

    StepMetaSet = apps.get_model("engine", "StepMetaSet")
    meta_set = StepMetaSet.objects.create(is_default=True, name="default")

    for meta in metas:
        meta_set.metas.add(meta)


def reverse_func(apps, schema_editor):
    StepMetaSet = apps.get_model("engine", "StepMetaSet")
    StepMetaSet.objects.filter(is_default=True, name="default").delete()

    DeployStepMeta = apps.get_model("engine", "DeployStepMeta")
    DeployStepMeta.objects.filter(
        name__in=[
            "解析应用进程信息",
            "上传仓库代码",
            "配置资源实例",
            "下载代码",
            "加载缓存",
            "构建应用",
            "检测进程类型",
            "制作打包构件",
            "上传缓存",
            "部署应用",
            "检测部署结果",
        ],
        buildpack_provider=None,
        builder_provider=None,
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('engine', '0004_auto_20210110_1917'),
    ]

    operations = [
        migrations.RunPython(forwards_func, reverse_func),
    ]