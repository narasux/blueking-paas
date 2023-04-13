/*
 * TencentBlueKing is pleased to support the open source community by making
 * 蓝鲸智云 - PaaS 平台 (BlueKing - PaaS System) available.
 * Copyright (C) 2017 THL A29 Limited, a Tencent company. All rights reserved.
 * Licensed under the MIT License (the "License"); you may not use this file except
 * in compliance with the License. You may obtain a copy of the License at
 *
 *	http://opensource.org/licenses/MIT
 *
 * Unless required by applicable law or agreed to in writing, software distributed under
 * the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
 * either express or implied. See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * We undertake not to change the open source license (MIT license) applicable
 * to the current version of the project delivered to anyone in the future.
 */

package handler

const (
	// AppTypeDefault 普通应用
	AppTypeDefault = "default"

	// AppTypeCNative 云原生应用
	AppTypeCNative = "cloud_native"
)

// AppInfo 应用信息接口
type AppInfo interface {
	// String 将应用信息转换成可打印展示的字符串
	String() string
}

// DeployResult 部署结果接口
type DeployResult interface {
	// String 将部署结果转换成可打印展示的字符串
	String() string
}

// DeployHistory 部署历史接口
type DeployHistory interface {
	// String 将部署结果转换成可打印展示的字符串
	String() string
}

// Deployer 部署器接口
type Deployer interface {
	// Exec 下发部署命令
	Exec(opts DeployOptions) (map[string]any, error)
	// GetResult 获取应用部署结果
	GetResult(opts DeployOptions) (DeployResult, error)
	// GetHistory 获取应用部署历史
	GetHistory(opts DeployOptions) (DeployHistory, error)
}

// Retriever 各类应用信息查询接口
type Retriever interface {
	// Exec 请求 PaaS API，获取应用某类信息
	Exec(appCode string) (AppInfo, error)
}