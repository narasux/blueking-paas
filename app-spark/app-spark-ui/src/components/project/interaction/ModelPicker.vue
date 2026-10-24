<template>
  <div class="model-picker">
    <span
      v-if="!switchable"
      class="model-picker__trigger is-readonly"
      :title="label"
    >
      <span class="model-picker__name">{{ label }}</span>
    </span>
    <bk-popover
      v-else
      ref="popoverRef"
      trigger="click"
      placement="top-end"
      theme="light"
      ext-cls="model-picker-popover"
      :arrow="false"
      :padding="0"
      :offset="8"
      @after-show="handleShow"
      @after-hidden="handleHidden"
    >
      <button
        type="button"
        class="model-picker__trigger"
        :title="label"
        aria-haspopup="listbox"
        :aria-expanded="visible"
      >
        <span class="model-picker__name">{{ label }}</span>
        <AngleDown class="model-picker__arrow" :class="{ 'is-open': visible }" />
      </button>
      <template #content>
        <div class="model-picker__panel">
          <div class="model-picker__search">
            <Search class="model-picker__search-icon" />
            <input
              ref="searchRef"
              v-model="keyword"
              class="model-picker__search-input"
              type="text"
              placeholder="搜索模型关键字"
              aria-label="搜索模型关键字"
              autocomplete="off"
            >
          </div>
          <p v-if="!visibleModels.length" class="model-picker__empty">
            没有匹配的模型
          </p>
          <ul v-else class="model-picker__list" role="listbox">
            <li v-for="item in visibleModels" :key="item.code">
              <button
                type="button"
                class="model-picker__item"
                :class="{ 'is-selected': item.code === selectedCode }"
                role="option"
                :aria-selected="item.code === selectedCode"
                :title="item.name"
                @click="handleSelect(item.code)"
              >
                <span class="model-picker__item-name">{{ item.name }}</span>
                <span v-if="item.supports_vision" class="model-picker__badge">多模态</span>
              </button>
            </li>
          </ul>
        </div>
      </template>
    </bk-popover>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue';
import { AngleDown, Search } from 'bkui-vue/lib/icon';
import { usePopoverVisible } from '@/composables/use-popover-visible';
import { useModelCatalog } from '@/composables/use-model-catalog';
import { filterModelsByKeyword } from './model-selection';

const keyword = ref('');
const searchRef = ref<HTMLInputElement>();
const {
  popoverRef,
  visible,
  onAfterShow,
  onAfterHidden,
  close,
} = usePopoverVisible();
const {
  catalog,
  failed,
  selectedCode,
  loadModelCatalog,
  selectModel,
} = useModelCatalog();

const switchable = computed(() => catalog.value?.switchable === true && !failed.value);
const selected = computed(() => (
  catalog.value?.models.find(model => model.code === selectedCode.value) ?? null
));
const label = computed(() => {
  if (failed.value) return '默认模型';
  if (!catalog.value) return '…';
  return selected.value?.name || '默认模型';
});
const visibleModels = computed(() => filterModelsByKeyword(catalog.value?.models ?? [], keyword.value));

onMounted(() => {
  // 进对话页拉一次。打开弹层不再请求。
  loadModelCatalog();
});

const handleShow = async () => {
  onAfterShow();
  keyword.value = '';
  await nextTick();
  searchRef.value?.focus();
};

const handleHidden = () => {
  onAfterHidden();
  keyword.value = '';
};

const handleSelect = (code: string) => {
  selectModel(code);
  close();
};
</script>

<style lang="postcss" scoped>
.model-picker {
  display: flex;
  min-width: 0;
  max-width: 200px;
}

.model-picker__trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  max-width: 100%;
  height: 32px;
  padding: 0 10px;
  color: #4d5464;
  font-family: inherit;
  font-size: 13px;
  font-weight: 500;
  background: #f0f1f5;
  border: 0;
  border-radius: 8px;
  cursor: pointer;
}

.model-picker__trigger.is-readonly {
  cursor: default;
}

.model-picker__trigger:hover:not(.is-readonly) {
  background: #e6e8ee;
}

.model-picker__trigger:focus-visible {
  outline: 2px solid var(--accent, #3d6dff);
  outline-offset: 2px;
}

.model-picker__name,
.model-picker__item-name {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.model-picker__arrow {
  flex-shrink: 0;
  color: #979ba5;
  font-size: 12px;
  transform: rotate(180deg);
  transition: transform 0.15s ease;
}

.model-picker__arrow.is-open {
  transform: none;
}

.model-picker__panel {
  width: min(360px, calc(100vw - 32px));
  overflow: hidden;
  background: #fff;
  border: 1px solid #e4e9f1;
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(18, 20, 26, 0.12);
}

.model-picker__search {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 44px;
  padding: 0 12px;
  border-bottom: 2px solid #3a84ff;
}

.model-picker__search-icon {
  flex-shrink: 0;
  color: #b7c4e0;
  font-size: 16px;
}

.model-picker__search-input {
  flex: 1;
  min-width: 0;
  padding: 0;
  color: #313238;
  font-family: inherit;
  font-size: 14px;
  background: transparent;
  border: 0;
}

.model-picker__search-input::placeholder {
  color: #b7c4e0;
}

.model-picker__search-input:focus {
  outline: none;
}

.model-picker__list {
  max-height: min(320px, 50vh);
  margin: 0;
  padding: 4px 0;
  overflow-y: auto;
  list-style: none;
}

.model-picker__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 40px;
  padding: 0 12px;
  color: #313238;
  font-family: inherit;
  font-size: 14px;
  text-align: left;
  background: transparent;
  border: 0;
  cursor: pointer;
}

.model-picker__item:hover:not(.is-selected) {
  background: #f5f7fa;
}

.model-picker__item.is-selected {
  color: #3a64f6;
  background: #eef2ff;
}

.model-picker__item-name {
  flex: 1;
  min-width: 0;
}

.model-picker__badge {
  flex-shrink: 0;
  height: 20px;
  padding: 0 6px;
  color: #d4880f;
  font-size: 12px;
  line-height: 20px;
  background: #fff4e6;
  border-radius: 4px;
}

.model-picker__empty {
  margin: 0;
  padding: 28px 12px;
  color: #979ba5;
  font-size: 13px;
  text-align: center;
}
</style>

<style lang="postcss">
.model-picker-popover,
.model-picker-popover.bk-popover,
.model-picker-popover .bk-pop2-content {
  padding: 0 !important;
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
}
</style>
