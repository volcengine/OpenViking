// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

export const DROID_API_VERSION = '1.0.0';
export const DROID_PROTOCOL_VERSION = '1.264.0';
export const DROID_NOT_LOGGED_IN = 'DROID_NOT_LOGGED_IN';

/**
 * Validate `droid.initialize_session` result shape.
 * @param {unknown} result
 * @returns {string} empty string when compatible, or reason.
 */
export function droidHandshakeProblem(result) {
  if (!result || typeof result !== 'object' || Array.isArray(result)) {
    return 'Droid rejected initialize_session';
  }
  if (typeof result.sessionId !== 'string' || !result.sessionId) {
    return 'Droid initialize_session returned no sessionId';
  }
  if (!Array.isArray(result.availableModels)) {
    return 'Droid initialize_session returned no availableModels';
  }
  return '';
}

/**
 * Map Droid's `availableModels` / `droid.list_models` entries into SuperAgent
 * catalog items with per-model reasoning effort capabilities.
 */
export function parseDroidModels(rawModels, currentModel = '') {
  if (!Array.isArray(rawModels)) return [];
  const entries = [];
  for (const item of rawModels) {
    if (!item || typeof item !== 'object') continue;
    const id = String(item.modelId ?? item.id ?? '').trim();
    if (!id) continue;
    if (item.disabled === true && id !== currentModel) continue;
    if (item.deprecated === true && id !== currentModel) continue;
    const displayName = typeof item.displayName === 'string'
      ? item.displayName
      : (typeof item.shortDisplayName === 'string' ? item.shortDisplayName : '');
    const efforts = Array.isArray(item.supportedReasoningEfforts)
      ? item.supportedReasoningEfforts.map(String).filter(Boolean)
      : [];
    const defaultEffort = typeof item.defaultReasoningEffort === 'string' ? item.defaultReasoningEffort : '';
    entries.push({
      id,
      ...(displayName ? { displayName } : {}),
      ...(id === currentModel ? { isDefault: true } : {}),
      effortsKnown: Array.isArray(item.supportedReasoningEfforts),
      efforts,
      ...(defaultEffort ? { defaultEffort } : {}),
    });
  }
  return entries;
}

/**
 * Normalize Droid stream-jsonrpc error codes for classification & auth recovery.
 */
export function normalizeDroidError(error) {
  if (!error || typeof error !== 'object') return error;
  const message = String(error.message ?? '');
  if (/authentication failed|not logged in|please log in|FACTORY_API_KEY|unauthorized/i.test(message)) {
    const mapped = new Error('droid is not logged in: run droid and sign in or set FACTORY_API_KEY');
    mapped.code = DROID_NOT_LOGGED_IN;
    return mapped;
  }
  if (/unsupported.*effort|reasoning.?effort.*(?:not supported|invalid)|invalid.*reasoning.?effort/i.test(message)) {
    error.code = 'UNSUPPORTED_REASONING_EFFORT';
    return error;
  }
  if (/\b(?:unknown|invalid|unsupported|unavailable)\s+model\b|\bmodel\b[^\n]{0,120}\bnot\s+(?:found|available|supported)\b/i.test(message)) {
    error.code = 'MODEL_UNAVAILABLE';
  }
  return error;
}
