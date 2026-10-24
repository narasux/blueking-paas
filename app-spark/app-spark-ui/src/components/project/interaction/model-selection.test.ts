import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  MODEL_CODE_STORAGE_KEY,
  clearStoredModelCode,
  filterModelsByKeyword,
  readStoredModelCode,
  resolveModelCodeForRequest,
  resolveSelectedModelCode,
  writeStoredModelCode,
} from './model-selection.ts';

const models = [
  { code: 'GLM-5_3-Online-32k', name: 'GLM-5_3-Online-32k' },
  { code: 'hy3', name: 'hy3' },
  { code: 'aidev-chat-auto', name: 'aidev-chat-auto' },
];

const memoryStorage = () => {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
    removeItem: (key: string) => {
      data.delete(key);
    },
  };
};

test('keeps the stored model when it is still listed', () => {
  assert.equal(resolveSelectedModelCode(models, 'GLM-5_3-Online-32k', 'hy3'), 'hy3');
});

test('falls back to the default when the stored model is gone', () => {
  assert.equal(resolveSelectedModelCode(models, 'hy3', 'retired'), 'hy3');
});

test('falls back to the first model when the default is also gone', () => {
  assert.equal(resolveSelectedModelCode(models, 'missing', null), 'GLM-5_3-Online-32k');
});

test('returns null when the list is empty', () => {
  assert.equal(resolveSelectedModelCode([], 'hy3', 'hy3'), null);
});

test('search matches name or code, ignoring case, and keeps the API order', () => {
  assert.deepEqual(
    filterModelsByKeyword(models, 'glm').map(model => model.code),
    ['GLM-5_3-Online-32k'],
  );
  assert.deepEqual(
    filterModelsByKeyword(models, 'Hy').map(model => model.code),
    ['hy3'],
  );
  assert.deepEqual(filterModelsByKeyword(models, 'zzz'), []);
  assert.deepEqual(
    filterModelsByKeyword(models, '  e').map(model => model.code),
    ['GLM-5_3-Online-32k', 'aidev-chat-auto'],
  );
});

test('an empty keyword returns every model in the same order', () => {
  assert.deepEqual(filterModelsByKeyword(models, '   ').map(model => model.code), [
    'GLM-5_3-Online-32k',
    'hy3',
    'aidev-chat-auto',
  ]);
});

test('a switchable catalog sends the selected code', () => {
  assert.equal(resolveModelCodeForRequest({ switchable: true }, 'hy3', false), 'hy3');
});

test('a read-only catalog and a failed list omit the model code', () => {
  assert.equal(resolveModelCodeForRequest({ switchable: false }, 'hy3', false), undefined);
  assert.equal(resolveModelCodeForRequest({ switchable: true }, 'hy3', true), undefined);
  assert.equal(resolveModelCodeForRequest(null, 'hy3', false), undefined);
  assert.equal(resolveModelCodeForRequest({ switchable: true }, null, false), undefined);
});

test('stored model code round-trips and can be cleared', () => {
  const storage = memoryStorage();
  assert.equal(readStoredModelCode(storage), null);
  writeStoredModelCode('hy3', storage);
  assert.equal(storage.getItem(MODEL_CODE_STORAGE_KEY), 'hy3');
  assert.equal(readStoredModelCode(storage), 'hy3');
  clearStoredModelCode(storage);
  assert.equal(readStoredModelCode(storage), null);
});
