// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

export const COPILOT_NOT_LOGGED_IN = 'COPILOT_NOT_LOGGED_IN';

/**
 * Verify that an ACP `initialize` reply came from GitHub Copilot CLI.
 * @param {unknown} result - initialize response result.
 * @returns {string} empty string when compatible, or a diagnostic message.
 */
export function copilotHandshakeProblem(result) {
  if (!result || typeof result !== 'object' || Array.isArray(result)) {
    return 'Copilot rejected initialize';
  }
  const name = String(result?.agentInfo?.name ?? '');
  if (!/copilot/i.test(name)) {
    return name ? `unrecognised ACP agent: ${name}` : 'ACP server did not identify itself as Copilot';
  }
  return '';
}

/**
 * Parse Copilot ACP `configOptions` (or fallback `models` payload) into
 * SuperAgent's normalized model catalog and dynamic effort metadata.
 * Non-reasoning models omit `reasoning_effort` from `configOptions`, so
 * per-model effort capabilities are cached dynamically as models are seen.
 */
export function parseCopilotConfigOptions(result, capabilitiesCache = new Map()) {
  const configOptions = Array.isArray(result?.configOptions) ? result.configOptions : [];
  const modelOption = configOptions.find((option) => option?.id === 'model' || option?.category === 'model');
  const effortOption = configOptions.find((option) => option?.id === 'reasoning_effort' || option?.category === 'thought_level');

  const rawOptions = Array.isArray(modelOption?.options)
    ? modelOption.options
    : (Array.isArray(result?.models?.availableModels)
      ? result.models.availableModels.map((entry) => ({ value: entry?.modelId, name: entry?.name }))
      : []);

  const currentModel = typeof modelOption?.currentValue === 'string'
    ? modelOption.currentValue
    : (typeof result?.models?.currentModelId === 'string' ? result.models.currentModelId : '');

  const currentEfforts = Array.isArray(effortOption?.options)
    ? effortOption.options.map((option) => String(option?.value ?? '')).filter(Boolean)
    : [];
  const currentEffort = typeof effortOption?.currentValue === 'string' ? effortOption.currentValue : '';

  if (currentModel && configOptions.length > 0) {
    capabilitiesCache.set(currentModel, effortOption
      ? { effortsKnown: true, efforts: currentEfforts, defaultEffort: currentEffort }
      : { effortsKnown: true, efforts: [], defaultEffort: '' });
  }

  const models = rawOptions
    .filter((option) => option && typeof option.value === 'string' && option.value)
    .map((option) => {
      const cached = capabilitiesCache.get(option.value);
      return {
        id: option.value,
        ...(typeof option.name === 'string' ? { displayName: option.name } : {}),
        ...(option.value === currentModel ? { isDefault: true } : {}),
        ...(cached
          ? {
            effortsKnown: cached.effortsKnown,
            efforts: [...cached.efforts],
            ...(cached.defaultEffort ? { defaultEffort: cached.defaultEffort } : {}),
          }
          : { effortsKnown: false, efforts: [] }),
      };
    });

  return {
    models,
    currentModel,
    hasEffortOption: Boolean(effortOption),
    efforts: currentEfforts,
    currentEffort,
    configOptions,
  };
}

/**
 * Attach structured error codes to Copilot ACP failures so auth errors fail
 * loudly and unknown-model / effort errors classify cleanly.
 */
export function normalizeCopilotError(error) {
  if (!error || typeof error !== 'object') return error;
  const message = String(error.message ?? '');
  if (/authentication required|not logged in|please log in|sign in|unauthorized/i.test(message)) {
    const mapped = new Error('copilot is not logged in: run copilot login first');
    mapped.code = COPILOT_NOT_LOGGED_IN;
    return mapped;
  }
  if (/does not support reasoning_effort|unsupported.*effort|invalid.*reasoning_effort/i.test(message)) {
    error.code = 'UNSUPPORTED_REASONING_EFFORT';
    return error;
  }
  if (/\b(?:unknown|invalid|unsupported|unavailable)\s+model\b|\bmodel\b[^\n]{0,120}\bnot\s+(?:found|available|supported)\b/i.test(message)) {
    error.code = 'MODEL_UNAVAILABLE';
  }
  return error;
}
