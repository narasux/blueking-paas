/*
 * TencentBlueKing is pleased to support the open source community by making
 * 蓝鲸智云 - PaaS 平台 (BlueKing - PaaS System) available.
 * Copyright (C) 2017 THL A29 Limited, a Tencent company. All rights reserved.
 * Licensed under the MIT License (the "License"); you may not use this file except
 * in compliance with the License. You may obtain a copy of the License at
 *
 *     http://opensource.org/licenses/MIT
 *
 * Unless required by applicable law or agreed to in writing, software distributed under
 * the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
 * either express or implied. See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * We undertake not to change the open source license (MIT license) applicable
 * to the current version of the project delivered to anyone in the future.
 */

package main

import (
	"os"
	"path/filepath"

	"github.com/BurntSushi/toml"
	"github.com/buildpacks/lifecycle/launch"

	devlaunch "github.com/TencentBlueking/bkpaas/cnb-builder-shim/cmd/dev-launcher/launch"
	"github.com/TencentBlueking/bkpaas/cnb-builder-shim/pkg/appdesc"
	"github.com/TencentBlueking/bkpaas/cnb-builder-shim/pkg/logging"
	"github.com/TencentBlueking/bkpaas/cnb-builder-shim/pkg/utils"
)

// DefaultAppDir default app dir
var DefaultAppDir = utils.EnvOrDefault("CNB_APP_DIR", "/app")

func main() {
	logger := logging.Default()

	var md launch.Metadata

	if _, err := toml.DecodeFile(launch.GetMetadataFilePath("/layers"), &md); err != nil {
		logger.Error(err, "read metadata")
		os.Exit(1)
	}

	appDesc, err := appdesc.UnmarshalToAppDesc(filepath.Join(DefaultAppDir, "app_desc.yaml"))
	if err != nil {
		logger.Error(err, "parse invalid app_desc.yaml")
		os.Exit(1)
	}

	if err = devlaunch.Run(md.Processes, appDesc); err != nil {
		logger.Error(err, "hot launch")
		os.Exit(1)
	}
}
