<template>
  <div class="right-main">
    <module-top-bar
      :key="topBarIndex"
      :app-code="appCode"
      :title="$t('模块配置')"
      :can-create="canCreateModule"
      :cur-module="curAppModule"
      :module-list="curAppModuleList"
      :first-module-name="firstTabActiveName"
      :active-route-name="active"
      @tab-change="handleTabChange"
    />
    <section :class="[{ 'enhanced-service-main-cls': !isTab }, { 'cloud-native-app': isCloudNativeApp }]">
      <paas-content-loader
        :placeholder="loaderPlaceholder"
        :offset-top="30"
        class="app-container middle overview"
        :class="{ 'enhanced-service': !isTab }"
      >
        <template v-if="!isTab">
          <div
            class="top-return-bar flex-row align-items-center"
            @click="handleGoBack"
          >
            <i
              class="paasng-icon paasng-arrows-left icon-cls-back mr5"
            />
            <h4>{{ $t('返回上一页') }}</h4>
          </div>
          <!-- 动态数据 -->
          <bk-alert
            v-if="instanceTipsConfig.isShow"
            class="instance-alert-cls"
            type="info"
            :title="instanceTipsConfig.tips">
          </bk-alert>
        </template>

        <section :class="['deploy-panel', 'deploy-main', 'mt5', { 'instance-details-cls': !isTab }]">
          <!-- 增强服务实例详情隐藏tab -->
          <bk-tab
            v-show="isTab"
            ext-cls="deploy-tab-cls"
            :active.sync="active"
            @tab-change="handleGoPage"
          >
            <template slot="setting">
              <bk-button
                class="pr20"
                text
                @click="handleYamlView"
              >
                {{ $t('查看YAML') }}
              </bk-button>
            </template>
            <bk-tab-panel
              v-for="(panel, index) in curTabPanels"
              v-bind="panel"
              :key="index"
            ></bk-tab-panel>
          </bk-tab>

          <div class="deploy-content">
            <router-view
              :ref="routerRefs"
              :key="renderIndex"
              :save-loading="buttonLoading"
              :is-component-btn="!isFooterActionBtn"
              @cancel="handleCancel"
              @hide-tab="isTab = false"
              @tab-change="handleGoPage"
              @set-markdown="setMarkdown"
              @set-tooltips="setTooltips"
              @set-loading="setMarkdownLoading"
            />
          </div>
        </section>


      </paas-content-loader>

      <usage-guide
        v-if="isShowUsageGuide && isCloudNativeApp"
        :data="serviceMarkdown"
        :is-cloud-native="isCloudNativeApp"
        :is-loading="isMarkdownLoading"
      />
    </section>

    <bk-dialog
      v-model="deployDialogConfig.visible"
      theme="primary"
      :width="deployDialogConfig.dialogWidth"
      ext-cls="deploy-dialog"
      title="YAML"
      header-position="left"
      :position="{ top: deployDialogConfig.top }"
      :show-footer="false"
    >
      <deployYaml
        :height="deployDialogConfig.height"
        :cloud-app-data="dialogCloudAppData"
      />
    </bk-dialog>
  </div>
</template>

<script>import moduleTopBar from '@/components/paas-module-bar';
import appBaseMixin from '@/mixins/app-base-mixin.js';
import deployYaml from './deploy-yaml';
import usageGuide from '@/components/usage-guide';
import { throttle } from 'lodash';
import { bus } from '@/common/bus';

export default {
  components: {
    moduleTopBar,
    deployYaml,
    usageGuide,
  },
  mixins: [appBaseMixin],
  data() {
    return {
      renderIndex: 0,
      buttonLoading: false,
      deployDialogConfig: {
        visible: false,
        dialogWidth: 1200,
        top: 120,
        height: 600,
      },
      manifestExt: {},
      panels: [
        { name: 'cloudAppDeployForBuild', label: this.$t('构建配置'), ref: 'build' },
        { name: 'cloudAppDeployForProcess', label: this.$t('进程配置'), ref: 'process' },
        { name: 'cloudAppDeployForEnv', label: this.$t('环境变量'), ref: 'env' },
        { name: 'cloudAppDeployForVolume', label: this.$t('挂载卷'), ref: 'volume' },
        { name: 'observabilityConfig', label: this.$t('可观测性'), ref: 'observability' },
        { name: 'appServices', label: this.$t('增强服务'), ref: 'services' },
        { name: 'moduleInfo', label: this.$t('更多配置'), ref: 'info' },
      ],
      active: 'cloudAppDeployForBuild',
      envValidate: true,
      isTab: true,
      dialogCloudAppData: [],
      topBarIndex: 0,
      isShowUsageGuide: false,
      serviceMarkdown: `## ${this.$t('暂无使用说明')}`,
      instanceTipsConfig: {},
      isMarkdownLoading: false,
    };
  },
  computed: {
    routeName() {
      return this.$route.name;
    },

    userFeature() {
      return this.$store.state.userFeature;
    },

    loaderPlaceholder() {
      if (this.routeName === 'appDeployForStag' || this.routeName === 'appDeployForProd') {
        return 'deploy-loading';
      }
      if (this.routeName === 'appDeployForHistory') {
        return 'deploy-history-loading';
      }
      return 'deploy-top-loading';
    },

    routerRefs() {
      const curPenel = this.curTabPanels.find(e => e.name === this.active);
      return curPenel ? curPenel.ref : 'process';
    },

    curAppModuleList() {
      // 根据name的英文字母排序
      return (this.$store.state.curAppModuleList || []).sort((a, b) => a.name.localeCompare(b.name));
    },

    isPageEdit() {
      return this.$store.state.cloudApi.isPageEdit;
    },

    firstTabActiveName() {
      return this.curTabPanels[0].name;
    },

    curTabPanels() {
      // 可观测性配置接入featureflag
      if (!this.userFeature.PHALANX) {
        this.panels = this.panels.filter(v => v.ref !== 'observability');
      }
      return this.panels;
    },

    // 是否需要保存操作按钮
    isFooterActionBtn() {
      // 无需展示外部操作按钮组
      const hideTabItems = ['cloudAppDeployForProcess', 'cloudAppDeployForHook', 'cloudAppDeployForEnv'];
      return !hideTabItems.includes(this.active);
    },
  },
  watch: {
    '$route'() {
      // eslint-disable-next-line no-plusplus
      this.renderIndex++;
      this.$store.commit('cloudApi/updatePageEdit', false);
      if (this.isShowUsageGuide) {
        this.handleCloseUsageGuide();
      }
    },
    appCode() {
      this.topBarIndex += 1;
    },
  },
  created() {
    this.active = this.panels.find(e => e.ref === this.$route.meta.module)?.name || this.firstTabActiveName;
    // 默认第一项
    if (this.$route.name !== this.firstTabActiveName) {
      this.$router.push({
        ...this.$route,
        name: this.active || this.firstTabActiveName,
      });
    }
    bus.$on('show-usage-guide', () => {
      this.isShowUsageGuide = true;
    });
    bus.$on('close-usage-guide', () => {
      this.isShowUsageGuide = false;
    });
  },
  mounted() {
    this.handleWindowResize();
    this.handleResizeFun();
  },
  methods: {
    handleGoPage(routeName) {
      this.$store.commit('cloudApi/updatePageEdit', false); // 切换tab 页面应为查看页面
      this.active = routeName;
      this.$router.push({
        name: routeName,
      });
    },

    // 取消改变页面状态
    handleCancel() {
      this.$store.commit('cloudApi/updatePageEdit', false);
      if (this.$refs[this.routerRefs]?.handleCancel) {
        this.$refs[this.routerRefs]?.handleCancel();
      }
    },

    // 查看yaml
    async handleYamlView() {
      try {
        const res = await this.$store.dispatch('deploy/getAppYamlManiFests', {
          appCode: this.appCode,
          moduleId: this.curModuleId,
        });
        this.deployDialogConfig.visible = true;
        this.dialogCloudAppData = res;
      } catch (e) {
        this.$paasMessage({
          theme: 'error',
          message: e.detail || e.message,
        });
      } finally {
        this.isLoading = false;
      }
    },

    handleGoBack() {
      this.handleGoPage('appServices');
      this.isTab = true;
      this.handleCloseUsageGuide();
    },

    handleWindowResize() {
      window.addEventListener('resize', throttle(this.handleResizeFun, 100));
    },

    handleResizeFun() {
      if (window.innerWidth < 1366) {
        this.deployDialogConfig.dialogWidth = 800;
        this.deployDialogConfig.top = 80;
        this.deployDialogConfig.height = 400;
      } else {
        this.deployDialogConfig.dialogWidth = 1100;
        this.deployDialogConfig.top = 120;
        this.deployDialogConfig.height = 520;
      }
    },

    handleCloseUsageGuide() {
      this.isShowUsageGuide = false;
    },

    handleTabChange() {
      this.handleCloseUsageGuide();
      this.isTab = true;
    },

    setMarkdown(markdown) {
      this.serviceMarkdown = markdown;
    },

    setTooltips(data) {
      this.instanceTipsConfig = data;
    },

    setMarkdownLoading(loading) {
      this.isMarkdownLoading = loading;
    },
  },
};
</script>

<style lang="scss" scoped>
@import '../../../../../assets/css/components/conf.scss';
@import './index.scss';
.enhanced-service-main-cls.cloud-native-app {
  height: 100%;
  display: flex;
  .enhanced-service {
    flex: 1;
    min-width: 0;
  }
}

.title {
  font-size: 16px;
  color: #313238;
  height: 50px;
  background: #fff;
  line-height: 50px;
  padding: 0 24px;
}
.deploy-btn-wrapper {
  // position: absolute;
  // top: 77vh;
  margin-top: 20px;
  height: 50px;
  line-height: 50px;
  padding: 0 20px;
}

.deploy-dialog .stage-info {
  width: 100%;
  background-color: #f5f6fa;
  overflow-y: auto;
  border-left: 10px solid #ccc;
  padding: 6px 0 30px 12px;
  margin-top: 8px;

  .info-title {
    font-weight: 700;
    margin-bottom: 8px;
  }

  .info-tips {
    margin-bottom: 8px;
  }

  .info-label {
    display: inline-block;
    min-width: 65px;
  }
}

.deploy-tab-cls {
  /deep/ .bk-tab-section {
    padding: 10px !important;
    border: none;
  }
}

.deploy-panel.deploy-main {
  box-shadow: 0 2px 4px 0 #1919290d;

  &.instance-details-cls {
    height: auto;
    min-height: auto;
  }
}

.top-return-bar {
  background: #F5F7FA;
  cursor: pointer;
  margin-bottom: 16px;
  h4 {
    font-size: 14px;
    color: #313238;
    font-weight: 400;
    padding: 0;
  }
  .icon-cls-back{
    color: #3A84FF;
    font-size: 14px;
    font-weight: bold;
  }
}
.instance-alert-cls {
  margin-bottom: 16px;
}
</style>
<style lang="scss">
.deploy-dropdown-menu .bk-dropdown-content {
  display: none !important;
}
.guide-link {
  color: #3a84ff;
  font-size: 12px;
  cursor: pointer;
}
</style>
