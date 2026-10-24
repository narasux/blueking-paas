/** 对话框模型选择：选中回显、搜索、以及这一轮要不要把模型编码带上。 */

export const MODEL_CODE_STORAGE_KEY = 'app-spark.selected-model';

export interface SelectableModel {
  code: string;
  name: string;
}

export interface ModelChoiceState {
  switchable: boolean;
}

type KeyValueStore = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

/**
 * 决定按钮上显示哪一个模型。
 *
 * 本地记下的编码还在这份列表里就用它；已经下架就回到接口给的默认模型；默认模型也不在列表里
 * 时用接口返回的第一个。三个都没有（列表是空的）则返回 null。
 */
export const resolveSelectedModelCode = (
  models: readonly SelectableModel[],
  defaultCode: string,
  storedCode: string | null,
): string | null => {
  if (storedCode && models.some(model => model.code === storedCode)) {
    return storedCode;
  }

  if (models.some(model => model.code === defaultCode)) {
    return defaultCode;
  }

  return models[0]?.code ?? null;
};

/**
 * 按名称或编码做不区分大小写的包含匹配，并保持接口返回的顺序。
 *
 * 顺序是后端推荐列表定的，搜索只过滤，不重排。关键字为空时原样返回。
 */
export const filterModelsByKeyword = <T extends SelectableModel>(
  models: readonly T[],
  keyword: string,
): T[] => {
  const text = keyword.trim().toLowerCase();
  if (!text) return [...models];

  return models.filter(model => (
    model.name.toLowerCase().includes(text) || model.code.toLowerCase().includes(text)
  ));
};

/**
 * 这一轮请求要带的模型编码。
 *
 * 只读部署和列表没拉下来时都不带，让后端用它自己的默认模型，对话不会被选择器挡住。
 */
export const resolveModelCodeForRequest = (
  catalog: ModelChoiceState | null,
  selectedCode: string | null,
  failed: boolean,
): string | undefined => {
  if (failed || !catalog?.switchable || !selectedCode) return undefined;

  return selectedCode;
};

export const readStoredModelCode = (storage: KeyValueStore = localStorage): string | null => {
  try {
    return storage.getItem(MODEL_CODE_STORAGE_KEY) || null;
  } catch {
    // 隐私模式可能禁用本地存储。记不住就用默认模型，不耽误发消息。
    return null;
  }
};

export const writeStoredModelCode = (code: string, storage: KeyValueStore = localStorage): void => {
  try {
    storage.setItem(MODEL_CODE_STORAGE_KEY, code);
  } catch {
    // 写不进去只影响下次刷新回显，这一轮的选择还在内存里。
  }
};

export const clearStoredModelCode = (storage: KeyValueStore = localStorage): void => {
  try {
    storage.removeItem(MODEL_CODE_STORAGE_KEY);
  } catch {
    // 清不掉时，下次刷新可能又选中这个已经不可用的模型。
  }
};
