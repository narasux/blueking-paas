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

package v1alpha1

import (
	"fmt"
	"regexp"

	"github.com/getsentry/sentry-go"
	"github.com/pkg/errors"
	"github.com/samber/lo"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/validation/field"
	ctrl "sigs.k8s.io/controller-runtime"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/webhook"

	"bk.tencent.com/paas-app-operator/pkg/utils/quota"
	"bk.tencent.com/paas-app-operator/pkg/utils/stringx"
)

// log is for logging in this package.
var appLog = logf.Log.WithName("bkapp-resource")

var (
	// AppNameRegex 应用名称格式（与 BK_APP_CODE 相同）
	AppNameRegex = regexp.MustCompile("^[a-z0-9-]{1,16}$")
	// ProcNameRegex 进程名称格式
	ProcNameRegex = regexp.MustCompile("^[a-z0-9]([-a-z0-9]){1,11}$")
)

// SetupWebhookWithManager ...
func (r *BkApp) SetupWebhookWithManager(mgr ctrl.Manager) error {
	return ctrl.NewWebhookManagedBy(mgr).For(r).Complete()
}

//+kubebuilder:webhook:path=/mutate-paas-bk-tencent-com-v1alpha1-bkapp,mutating=true,failurePolicy=fail,sideEffects=None,groups=paas.bk.tencent.com,resources=bkapps,verbs=create;update,versions=v1alpha1,name=mbkapp.kb.io,admissionReviewVersions=v1;v1beta1

var _ webhook.Defaulter = &BkApp{}

// Default 实现 webhook.Defaulter 接口用于预设默认值
func (r *BkApp) Default() {
	appLog.Info("default", "name", r.Name)

	// 为进程的端口号、CPU 内存资源等配置默认值
	r.Spec.Processes = lo.Map(r.Spec.Processes, func(proc Process, i int) Process {
		if proc.TargetPort == 0 {
			proc.TargetPort = ProcDefaultTargetPort
		}
		if proc.CPU == "" {
			proc.CPU = projConf.ResLimitConfig.ProcDefaultCPULimits
		}
		if proc.Memory == "" {
			proc.Memory = projConf.ResLimitConfig.ProcDefaultMemLimits
		}
		if proc.ImagePullPolicy == "" {
			proc.ImagePullPolicy = corev1.PullIfNotPresent
		}
		if proc.Autoscaling != nil && proc.Autoscaling.Enabled {
			// 若没有配置最小副本数，则设置为 1
			if proc.Autoscaling.MinReplicas == 0 {
				proc.Autoscaling.MinReplicas = 1
			}
			// 若没有配置最大副本数，则使用预设上限
			if proc.Autoscaling.MaxReplicas == 0 {
				proc.Autoscaling.MaxReplicas = projConf.ResLimitConfig.MaxReplicas
			}
			// 如果没有配置策略，使用默认值
			if proc.Autoscaling.Policy == "" {
				proc.Autoscaling.Policy = ScalingPolicyDefault
			}
		}
		return proc
	})
}

//+kubebuilder:webhook:path=/validate-paas-bk-tencent-com-v1alpha1-bkapp,mutating=false,failurePolicy=fail,sideEffects=None,groups=paas.bk.tencent.com,resources=bkapps,verbs=create;update;delete,versions=v1alpha1,name=vbkapp.kb.io,admissionReviewVersions=v1;v1beta1

var _ webhook.Validator = &BkApp{}

// ValidateCreate 应用创建时校验
func (r *BkApp) ValidateCreate() error {
	appLog.Info("validate create", "name", r.Name)
	err := r.validateApp()
	if err != nil {
		sentry.CaptureException(errors.Wrapf(err, "webhook validate bkapp [%s/%s] failed", r.Namespace, r.Name))
	}
	return err
}

// ValidateUpdate 应用更新时校验
func (r *BkApp) ValidateUpdate(old runtime.Object) error {
	appLog.Info("validate update", "name", r.Name)
	// TODO 更新校验逻辑，限制部分不可变字段（若存在）
	err := r.validateApp()
	if err != nil {
		sentry.CaptureException(errors.Wrapf(err, "webhook validate bkapp [%s/%s] failed", r.Namespace, r.Name))
	}
	return err
}

// ValidateDelete 应用删除时校验
func (r *BkApp) ValidateDelete() error {
	appLog.Info("validate delete (do nothing)", "name", r.Name)
	// TODO: 删除时候暂时不做任何校验，后续可以考虑支持删除保护？
	return nil
}

func (r *BkApp) validateApp() error {
	var allErrs field.ErrorList

	if err := r.validateAppName(); err != nil {
		allErrs = append(allErrs, err)
	}
	if err := r.validateAppSpec(); err != nil {
		allErrs = append(allErrs, err)
	}
	if err := r.validateEnvOverlay(); err != nil {
		allErrs = append(allErrs, err)
	}
	if len(allErrs) == 0 {
		return nil
	}
	return apierrors.NewInvalid(GroupKindBkApp, r.Name, allErrs)
}

// 应用名称必须符合正则（规则同 BK_APP_CODE）
func (r *BkApp) validateAppName() *field.Error {
	if matched := AppNameRegex.MatchString(r.Name); !matched {
		return field.Invalid(
			field.NewPath("metadata").Child("name"), r.Name, "must match regex "+AppNameRegex.String(),
		)
	}
	return nil
}

func (r *BkApp) validateAppSpec() *field.Error {
	procsField := field.NewPath("spec").Child("processes")
	if len(r.Spec.Processes) == 0 {
		return field.Invalid(procsField, r.Spec.Processes, "processes can't be empty")
	}

	procCounter := map[string]int{}
	for idx, proc := range r.Spec.Processes {
		if err := r.validateAppProc(proc, idx); err != nil {
			return err
		}
		// 检查进程是否被重复定义
		procCounter[proc.Name]++
		if procCounter[proc.Name] > 1 {
			return field.Invalid(procsField, r.Spec.Processes, fmt.Sprintf(`process "%s" is duplicated`, proc.Name))
		}
	}
	// 至少需要包含一个 web 进程
	if procCounter[WebProcName] == 0 {
		return field.Invalid(procsField, r.Spec.Processes, `"web" process is required`)
	}

	// 环境变量中的键不能为空
	for idx, env := range r.Spec.Configuration.Env {
		path := field.NewPath("spec").Child("configuration").Child("env").Index(idx)
		if env.Name == "" {
			return field.Invalid(path.Child("name"), env.Name, "name can't be empty")
		}
	}
	return nil
}

// Get all process names
func (r *BkApp) getProcNames() []string {
	items := []string{}
	for _, proc := range r.Spec.Processes {
		items = append(items, proc.Name)
	}
	return items
}

func (r *BkApp) validateAppProc(proc Process, idx int) *field.Error {
	pField := field.NewPath("spec").Child("processes").Index(idx)
	// 1. 进程名称必须符合正则
	if matched := ProcNameRegex.MatchString(proc.Name); !matched {
		return field.Invalid(
			pField.Child("name"),
			proc.Name,
			"must match regex "+ProcNameRegex.String(),
		)
	}
	// 2. 副本数量不能超过上限
	if *proc.Replicas > projConf.ResLimitConfig.MaxReplicas {
		return field.Invalid(
			pField.Child("replicas"),
			*proc.Replicas,
			fmt.Sprintf("at most support %d replicas", projConf.ResLimitConfig.MaxReplicas),
		)
	}
	// 3. 资源配额需要符合规范
	if _, err := quota.NewQuantity(proc.CPU, quota.CPU); err != nil {
		return field.Invalid(pField.Child("cpu"), proc.CPU, err.Error())
	}
	if _, err := quota.NewQuantity(proc.Memory, quota.Memory); err != nil {
		return field.Invalid(pField.Child("memory"), proc.Memory, err.Error())
	}
	// 4. 进程镜像不可为空
	if proc.Image == "" {
		return field.Invalid(pField.Child("image"), proc.Image, "process image is required")
	}
	// 5. 如果启用扩缩容，需要符合规范
	if proc.Autoscaling != nil && proc.Autoscaling.Enabled {
		// 目前不支持缩容到 0
		if proc.Autoscaling.MinReplicas <= 0 {
			return field.Invalid(
				pField.Child("autoscaling").Child("minReplicas"),
				proc.Autoscaling.MinReplicas,
				"minReplicas must be greater than 0",
			)
		}
		// 扩缩容最大副本数不可超过上限
		if proc.Autoscaling.MaxReplicas > projConf.ResLimitConfig.MaxReplicas {
			return field.Invalid(
				pField.Child("autoscaling").Child("maxReplicas"),
				proc.Autoscaling.MaxReplicas,
				fmt.Sprintf("at most support %d replicas", projConf.ResLimitConfig.MaxReplicas),
			)
		}
		// 最大副本数需大于等于最小副本数
		if proc.Autoscaling.MinReplicas > proc.Autoscaling.MaxReplicas {
			return field.Invalid(
				pField.Child("autoscaling").Child("maxReplicas"),
				proc.Autoscaling.MaxReplicas,
				"maxReplicas must be greater than or equal to minReplicas",
			)
		}
		// 目前必须配置扩缩容策略
		if proc.Autoscaling.Policy == "" {
			return field.Invalid(
				pField.Child("autoscaling").Child("policy"),
				proc.Autoscaling.Policy,
				"autoscaling policy is required",
			)
		}
		// 配置的扩缩容策略必须是受支持的
		if !lo.Contains(AllowedScalingPolicies, proc.Autoscaling.Policy) {
			return field.NotSupported(
				pField.Child("autoscaling").Child("policy"),
				proc.Autoscaling.Policy, stringx.ToStrArray(AllowedScalingPolicies),
			)
		}
	}
	return nil
}

// Validate Spec.EnvOverlay field
func (r *BkApp) validateEnvOverlay() *field.Error {
	if r.Spec.EnvOverlay == nil {
		return nil
	}

	f := field.NewPath("spec").Child("envOverlay")

	// Validate "envVariables": envName
	for i, env := range r.Spec.EnvOverlay.EnvVariables {
		envField := f.Child("envVariables").Index(i)
		if !env.EnvName.IsValid() {
			return field.Invalid(envField.Child("envName"), env.EnvName, "envName is invalid")
		}
	}

	// Validate "replicas": envName and process
	maxReplicas := projConf.ResLimitConfig.MaxReplicas
	for i, rep := range r.Spec.EnvOverlay.Replicas {
		replicasField := f.Child("replicas").Index(i)
		if !rep.EnvName.IsValid() {
			return field.Invalid(replicasField.Child("envName"), rep.EnvName, "envName is invalid")
		}
		if !lo.Contains(r.getProcNames(), rep.Process) {
			return field.Invalid(replicasField.Child("process"), rep.Process, "process name is invalid")
		}
		if rep.Count > maxReplicas {
			return field.Invalid(
				replicasField.Child("count"),
				rep.Process,
				fmt.Sprintf("count can't be greater than %d", maxReplicas),
			)
		}
	}

	// Validate "autoscaling": envName, process and policy
	for i, as := range r.Spec.EnvOverlay.Autoscaling {
		pField := f.Child("autoscaling").Index(i)
		if !as.EnvName.IsValid() {
			return field.Invalid(pField.Child("envName"), as.EnvName, "envName is invalid")
		}
		if !lo.Contains(r.getProcNames(), as.Process) {
			return field.Invalid(pField.Child("process"), as.Process, "process name is invalid")
		}
		// 添加的 envOverlay 需要配置扩缩容策略
		if as.Policy == "" {
			return field.Invalid(pField.Child("policy"), as.Policy, "autoscaling policy is required")
		}
		// 配置的扩缩容策略必须是受支持的
		if !lo.Contains(AllowedScalingPolicies, as.Policy) {
			return field.NotSupported(pField.Child("policy"), as.Policy, stringx.ToStrArray(AllowedScalingPolicies))
		}
	}
	return nil
}
