import { ref } from 'vue';

import {
  clearStoredModelCode,
  readStoredModelCode,
  resolveModelCodeForRequest,
  resolveSelectedModelCode,
  writeStoredModelCode,
} from '@/components/project/interaction/model-selection';
import { listModels } from '@/http/api';
import type { ModelCatalogResponse } from '@/http/types';

const catalog = ref<ModelCatalogResponse | null>(null);
const failed = ref(false);
const selectedCode = ref<string | null>(null);
let pending: Promise<void> | null = null;

const fetchCatalog = async () => {
  try {
    const data = await listModels();
    catalog.value = data;
    failed.value = false;
    selectedCode.value = resolveSelectedModelCode(data.models, data.default_code, readStoredModelCode());
  } catch (error) {
    // 列表失败不挡对话：按钮改成不可点的「默认模型」，发送时不带模型编码。
    catalog.value = null;
    failed.value = true;
    selectedCode.value = null;
    console.error('[model] 模型列表加载失败', error);
  }
};

/**
 * 拉取可选模型。
 *
 * 进入对话页时调一次。打开弹层不要再调：列表已经在手里。`force` 只给「所选模型已被拒绝」用。
 */
export const loadModelCatalog = async (force = false): Promise<void> => {
  if (!force && pending) return pending;

  const run = fetchCatalog();
  pending = run;
  try {
    await run;
  } finally {
    if (pending === run) pending = null;
  }
};

/** 用户点了一行。进行中的那一轮已经把编码放进请求体，这里只改按钮和下一次发送。 */
export const selectModel = (code: string) => {
  selectedCode.value = code;
  writeStoredModelCode(code);
};

/**
 * 发送被 422 拒绝后，丢掉这个编码并重新拉列表，选中回到默认模型。
 *
 * 先清本地存储：刷新如果失败，下次进页也不能再把这个编码自动选回来。
 */
export const reportModelUnavailable = async (): Promise<void> => {
  clearStoredModelCode();
  await loadModelCatalog(true);
  const current = catalog.value;
  if (!current) return;

  const next = resolveSelectedModelCode(current.models, current.default_code, null);
  selectedCode.value = next;
  if (next) writeStoredModelCode(next);
};

/** 此刻点击发送该带上的模型。读的是调用瞬间的选择，不受之后的改选影响。 */
export const resolveModelCodeForSend = (): string | undefined => (
  resolveModelCodeForRequest(catalog.value, selectedCode.value, failed.value)
);

export const useModelCatalog = () => ({
  catalog,
  failed,
  selectedCode,
  loadModelCatalog,
  selectModel,
});
