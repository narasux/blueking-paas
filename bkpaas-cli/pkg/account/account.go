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

package account

import (
	"net/http"

	"github.com/TencentBlueKing/gopkg/mapx"
	"github.com/levigross/grequests"
	"github.com/pkg/errors"

	"github.com/TencentBlueKing/blueking-paas/client/pkg/config"
)

// AuthApiErr Token 鉴权 API 异常
var AuthApiErr = errors.New("Auth API unavailable")

// AuthApiRespErr Token 鉴权 API 返回格式异常
var AuthApiRespErr = errors.New("Auth API response not json format")

// TokenExpiredOrInvalid Token 过期或无效
var TokenExpiredOrInvalid = errors.New("AccessToken expired or invalid")

// FetchUsernameFailedErr 其他无法获取用户名的情况
var FetchUsernameFailedErr = errors.New("Unable to fetch username")

// FetchUserNameByAccessToken 通过 AccessToken 获取用户名信息
func FetchUserNameByAccessToken(accessToken string) (string, error) {
	ro := grequests.RequestOptions{
		Params: map[string]string{"access_token": accessToken},
	}
	resp, err := grequests.Get(config.G.CheckTokenUrl, &ro)

	if resp.StatusCode != http.StatusOK || err != nil {
		return "", AuthApiErr
	}

	authResp := map[string]any{}
	if err = resp.JSON(&authResp); err != nil {
		return "", AuthApiRespErr
	}

	if !mapx.GetBool(authResp, "result") {
		return "", TokenExpiredOrInvalid
	}

	if rtx := mapx.GetStr(authResp, "data.id_providers.rtx.username"); rtx != "" {
		return rtx, nil
	}

	if uin := mapx.GetStr(authResp, "data.id_providers.uin.username"); uin != "" {
		return uin, nil
	}
	return "", FetchUsernameFailedErr
}
