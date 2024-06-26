<template>
  <div class="more-info mt20">
    <ul>
      <li
        class="info-item"
        v-for="(item, index) in viewData"
        :key="index"
      >
        <div class="label">{{ item.title }}</div>
        <div
          class="value"
          v-if="Array.isArray(item.default)"
        >
          {{ formatMultiple(item.default, item.key) }}
        </div>
        <div
          class="value"
          v-else
        >
          <template v-if="componentData[item.key] && componentData[item.key].length">
            {{ componentData[item.key].find(v => v.value === item.default || v.label === item.default)?.label || '--' }}
          </template>
          <template v-else>
            {{ item.default || '--' }}
          </template>
        </div>
      </li>
    </ul>
  </div>
</template>

<script>
export default {
  name: 'MoreInfo',
  data() {
    return {
      schemaFormData: {},
      schema: {},
      viewData: [],
      componentData: {},
    };
  },
  computed: {
    curPluginInfo() {
      return this.$store.state.plugin.curPluginInfo;
    },
    moreInfoFields() {
      return this.curPluginInfo.extra_fields;
    },
  },
  created() {
    this.fetchPluginTypeList();
  },
  methods: {
    // 获取更多信息表单数据
    async fetchPluginTypeList() {
      try {
        const results = await this.$store.dispatch('plugin/getPluginsTypeList');
        const curPluginType = results.find(v => v.plugin_type?.id === this.curPluginInfo.pd_id);

        if (!Object.keys(curPluginType.schema.extra_fields)?.length) {
          // 无extra_fields字段不展示
          this.$emit('show-info', false);
        }
        // 根据 extra_fields_order 字段排序
        const extraFields = this.sortdSchema(
          curPluginType.schema?.extra_fields_order || [],
          curPluginType.schema?.extra_fields,
        );
        this.viewData = [];
        // 对数据进行填充
        // eslint-disable-next-line no-restricted-syntax
        for (const key in extraFields) {
          const uiComponent = extraFields[key]['ui:component'];
          // 下拉选择列表数据，处理国际化翻译问题
          this.componentData[key] = uiComponent?.props?.datasource || [];
          if (this.moreInfoFields[key]) {
            extraFields[key].default = this.moreInfoFields[key];
          } else {
            const { type } = extraFields[key];
            if (type === 'array') {
              extraFields[key].default = [];
            }
          }
          // 查看态数据
          this.viewData.push({ title: extraFields[key].title, default: extraFields[key].default, key });
        }

        this.$emit('set-schema', { type: 'object', required: this.getRequiredFields(extraFields), properties: extraFields });
      } catch (e) {
        this.$paasMessage({
          theme: 'error',
          message: e.detail || e.message || this.$t('接口异常'),
        });
      }
    },
    /**
     * 根据 fieldsOrder 字段排序 schema
     * @param  {Array} fieldsOrder 排序字段列表
     * @param  {Object} properties 数据
     */
    sortdSchema(fieldsOrder = [], properties) {
      const sortdProperties = {};
      if (fieldsOrder.length) {
        fieldsOrder.map((key) => {
          sortdProperties[key] = properties[key];
        });
        return sortdProperties;
      }
      return properties;
    },
    // 获取必填项字段列表
    getRequiredFields(properties) {
      if (!Object.keys(properties).length) {
        return;
      }
      const requiredFields = [];
      for (const key in properties) {
        if (Object.prototype.hasOwnProperty.call(properties[key], 'ui:rules')) {
          requiredFields.push(key);
        }
      }
      return requiredFields;
    },
    // 格式化多选显示内容
    formatMultiple(data = [], key) {
      if (!data.length) {
        return '--';
      }
      const arr = [];
      data.forEach((v) => {
        const res = this.componentData[key].find(item => v === item.label || v === item.value)?.label;
        res && arr.push(res);
      });
      return arr.join(', ') || '--';
    },
  },
};
</script>

<style lang="scss" scoped>
.more-info {
  .info-item {
    font-size: 12px;
    display: flex;
    .label {
      display: flex;
      justify-content: center;
      align-items: center;
      width: 180px;
      height: 42px;
      border: 1px solid #dcdee5;
      background: #fafbfd;
    }
    .value {
      flex: 1;
      height: 42px;
      border: 1px solid #dcdee5;
      padding-left: 25px;
      padding-right: 10px;
      background: #fff;
      line-height: 42px;
      border-left: none;
    }
    &:not(:last-child) {
      .label,
      .value {
        border-bottom: none;
      }
    }
  }
}
</style>
