// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

function frozenStrings(values) {
  return Object.freeze([...values]);
}

// How a runtime keeps its own OpenViking plugin out, so that Vika's memory
// (the daemon and the Core's openviking_* tools) is the only one the agent
// sees. Every runtime must declare one (see ov-isolation.mjs and
// docs/openviking-ownership.md):
// - launch-args: the launcher passes arguments that switch the plugin off;
// - config-home: the launcher points the runtime at a Vika-owned config home;
// - closed: the runtime loads no plugins at all (Durable's own registry);
// - remote: the runtime runs elsewhere and loads nothing from this machine.
export const OV_ISOLATION = Object.freeze(['launch-args', 'config-home', 'closed', 'remote']);

function runtimeDefinition({
  id,
  aliases,
  directAliases = [],
  defaultModel = '',
  canonicalDefault = defaultModel,
  models = [],
  efforts = [],
  ovIsolation,
  capabilities,
}) {
  if (!OV_ISOLATION.includes(ovIsolation)) {
    throw new Error(`runtime ${id} must declare how it keeps its own OpenViking plugin out (ovIsolation: ${OV_ISOLATION.join(' | ')}); see docs/openviking-ownership.md`);
  }
  return Object.freeze({
    id,
    ovIsolation,
    aliases: frozenStrings(aliases),
    directAliases: frozenStrings(directAliases),
    defaultModel,
    canonicalDefault,
    models: frozenStrings(models),
    efforts: frozenStrings(efforts),
    capabilities: Object.freeze({ ...capabilities }),
  });
}

export const DEFAULT_RUNTIME_ID = 'codex';

// The base model-id rule: what `/model` accepts by default and what a select
// request may carry onto the wire.
//
// It is a character whitelist on purpose. A catalogue may report, and DSH may
// serve, ids outside it (`a b`, `c#d`, non-ASCII); accepting them here would
// widen every runtime's `/model` input, and a space or a `#` cannot be told
// apart from a typo in a chat command. Those ids stay valid configuration --
// nothing in this file rewrites or removes one -- and `/model` says so
// explicitly instead of pretending they do not exist.
//
// The bound applies to the COMPLETE id (route/model), which is what `/model`
// receives; a `dsh:`-style runtime qualifier names the runtime, is not part of
// the id, and never counts against the bound.
export const MODEL_ID_PATTERN = /^[A-Za-z0-9._:\/\[\]-]{1,256}$/;

// DSH is the one runtime with measured evidence that a route id may carry `+`
// (a real dsh 0.2.2-rc.1 lists and selects `m+v1`). The decision is to keep the
// original rule everywhere else and open `+` only on the path that needs it, so
// `/model` and the select path ask `modelIdPattern(runtime)` instead of using
// the base constant directly.
export const DSH_MODEL_ID_PATTERN = /^[A-Za-z0-9._:@+\/\[\]-]{1,256}$/;

// The one bound both rules share, read out of the pattern so a message can
// never promise a different limit from the rule it describes.
export const MODEL_ID_MAX_LENGTH = Number(/\{1,(\d+)\}/.exec(MODEL_ID_PATTERN.source)?.[1]) || 256;

const MODEL_ID_SYMBOLS = Object.freeze(['.', '_', ':', '/', '[', ']', '-']);
const DSH_MODEL_ID_SYMBOLS = Object.freeze(['.', '_', ':', '@', '+', '/', '[', ']', '-']);

function isDshRuntime(runtimeOrAlias) {
  return getRuntimeDefinition(runtimeOrAlias)?.id === 'dsh';
}

/**
 * The model-id rule for one runtime. DSH admits `+` on measured evidence; every
 * other runtime, and an unknown/omitted one, uses the base rule.
 * @param {unknown} [runtimeOrAlias] - runtime id or alias.
 * @returns {RegExp} the rule `/model` and the select path apply.
 */
export function modelIdPattern(runtimeOrAlias) {
  return isDshRuntime(runtimeOrAlias) ? DSH_MODEL_ID_PATTERN : MODEL_ID_PATTERN;
}

/**
 * The punctuation one runtime's rule admits, in the order user-facing messages
 * list them. Kept beside the patterns so a message cannot promise a character
 * the rule refuses, or hide one it accepts.
 * @param {unknown} [runtimeOrAlias] - runtime id or alias.
 * @returns {readonly string[]}
 */
export function modelIdSymbols(runtimeOrAlias) {
  return isDshRuntime(runtimeOrAlias) ? DSH_MODEL_ID_SYMBOLS : MODEL_ID_SYMBOLS;
}

/**
 * Assert one model id against the rule for a runtime.
 * @param {unknown} value - the id as typed or as reported by a catalogue.
 * @param {unknown} [runtimeOrAlias] - runtime id or alias; omitted means the base rule.
 * @returns {string} the same value, when it is valid.
 * @throws {Error} `Invalid model name: <value>` otherwise.
 */
export function assertModelId(value, runtimeOrAlias) {
  const raw = String(value ?? '');
  if (!modelIdPattern(runtimeOrAlias).test(raw)) throw new Error(`Invalid model name: ${raw}`);
  return raw;
}

/**
 * Which part of the rule an id breaks, so a caller can report length as length
 * and characters as characters instead of calling every refusal a character
 * problem.
 * @param {unknown} value - the id alone, with any runtime qualifier already removed.
 * @param {unknown} [runtimeOrAlias] - runtime id or alias; omitted means the base rule.
 * @returns {'empty'|'length'|'characters'|null} null when the id is valid.
 */
export function modelIdProblem(value, runtimeOrAlias) {
  const raw = String(value ?? '');
  if (!raw) return 'empty';
  if (raw.length > MODEL_ID_MAX_LENGTH) return 'length';
  return modelIdPattern(runtimeOrAlias).test(raw) ? null : 'characters';
}

export const RUNTIME_CATALOG = Object.freeze({
  codex: runtimeDefinition({
    id: 'codex',
    ovIsolation: 'launch-args',
    aliases: ['codex'],
    efforts: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh'],
    capabilities: {
      modelCatalog: 'dynamic',
      effortControl: 'selectable',
      activeInput: 'steer',
      scopePolicy: 'any',
      outputOwnership: 'runtime',
      sessionAuthority: 'preserve-process',
      // Where model actions go: the Core agent tools (MCP), or the Worker's lark-cli.
      agentTools: 'core',
    },
  }),
  traex: runtimeDefinition({
    id: 'traex',
    ovIsolation: 'launch-args',
    aliases: ['traex', 'trae', 'traecli', 'trae-cli'],
    directAliases: ['traex', 'trae', 'traecli', 'trae-cli'],
    canonicalDefault: 'traex',
    efforts: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
    capabilities: {
      modelCatalog: 'dynamic',
      effortControl: 'selectable',
      activeInput: 'steer',
      scopePolicy: 'any',
      outputOwnership: 'runtime',
      sessionAuthority: 'preserve-process',
      agentTools: 'core',
    },
  }),
  claude: runtimeDefinition({
    id: 'claude',
    ovIsolation: 'launch-args',
    aliases: ['claude'],
    canonicalDefault: 'claude',
    models: ['sonnet', 'opus', 'haiku', 'fable'],
    efforts: ['low', 'medium', 'high', 'xhigh', 'max'],
    capabilities: {
      modelCatalog: 'dynamic',
      effortControl: 'selectable',
      activeInput: 'steer',
      scopePolicy: 'any',
      outputOwnership: 'runtime',
      sessionAuthority: 'preserve-process',
      agentTools: 'core',
    },
  }),
  volc: runtimeDefinition({
    id: 'volc',
    ovIsolation: 'remote',
    aliases: ['volc', 'volcengine', 'ark'],
    directAliases: ['volc', 'volcengine', 'ark'],
    defaultModel: 'ark',
    models: ['ark'],
    efforts: ['managed'],
    capabilities: {
      modelCatalog: 'static',
      effortControl: 'remote-managed',
      activeInput: 'queue-next-turn',
      scopePolicy: 'single-conversation',
      outputOwnership: 'runtime',
      sessionAuthority: 'rotate',
      agentTools: 'lark-cli',
    },
  }),
  pi: runtimeDefinition({
    id: 'pi',
    ovIsolation: 'launch-args',
    aliases: ['pi'],
    directAliases: ['pi'],
    canonicalDefault: 'pi',
    efforts: ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
    capabilities: {
      modelCatalog: 'dynamic',
      effortControl: 'selectable',
      activeInput: 'steer',
      scopePolicy: 'any',
      outputOwnership: 'runtime',
      sessionAuthority: 'preserve-process',
      agentTools: 'core',
    },
  }),
  // Durable agent harness on @earendil-works/pi-durable. A host child process
  // owns one SQLite storage; sessions are durable conversations and the host
  // resumes a run the previous daemon generation left unfinished.
  durable: runtimeDefinition({
    id: 'durable',
    ovIsolation: 'closed',
    aliases: ['durable', 'pi-durable', 'pidurable'],
    directAliases: ['durable', 'pi-durable', 'pidurable'],
    canonicalDefault: 'durable',
    efforts: ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
    capabilities: {
      modelCatalog: 'dynamic',
      effortControl: 'selectable',
      activeInput: 'steer',
      scopePolicy: 'any',
      outputOwnership: 'runtime',
      sessionAuthority: 'preserve-process',
      agentTools: 'core',
    },
  }),
  dsh: runtimeDefinition({
    id: 'dsh',
    // Vika gives DSH a private DSH_HOME (`<dataDir>/dsh/home`) and its own
    // `--patch` overlay, so the runtime never reads the owner's `~/.dsh`
    // plugins, skills or credentials. That is config-home isolation: the flags
    // only select the profile and the patch file.
    ovIsolation: 'config-home',
    aliases: ['dsh', 'deepseek-harness'],
    directAliases: ['dsh', 'deepseek-harness'],
    // A bare `dsh` selector must resolve to a model the runtime can apply: an
    // empty default made /runtime dsh stage "" and then fail the
    // "target runtime did not apply the requested model" comparison.
    defaultModel: 'deepseek-official/deepseek-flash',
    canonicalDefault: 'dsh',
    // The static list is what attributes a bare model to this runtime and what
    // supplies the built-in production default. The live catalog comes from DSH
    // itself (bridge v2 `vika/llm/models`) and also serves the operator's own
    // routes (deepseek-chat and the like), so model selection is dynamic
    // rather than limited to this list. Efforts are the union DSH understands;
    // which of them a model offers is answered per model.
    models: ['deepseek-official/deepseek-flash', 'deepseek-official/deepseek-v4-pro'],
    efforts: ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
    capabilities: {
      modelCatalog: 'dynamic', effortControl: 'selectable', activeInput: 'steer',
      scopePolicy: 'any', outputOwnership: 'runtime', sessionAuthority: 'preserve-process',
      agentTools: 'core',
    },
  }),
  opencode: runtimeDefinition({
    id: 'opencode',
    ovIsolation: 'config-home',
    aliases: ['opencode'],
    directAliases: ['opencode'],
    canonicalDefault: 'opencode',
    // OpenCode 1 ACP exposes no daemon-side reasoning-effort API; reasoning is
    // selected per model variant. Its session/prompt serializes turns, so
    // messages arriving mid-turn run on the next one.
    capabilities: {
      modelCatalog: 'dynamic',
      effortControl: 'unsupported',
      activeInput: 'queue-next-turn',
      scopePolicy: 'any',
      outputOwnership: 'runtime',
      sessionAuthority: 'preserve-process',
      agentTools: 'core',
    },
  }),
  agy: runtimeDefinition({
    id: 'agy',
    ovIsolation: 'config-home',
    aliases: ['agy', 'antigravity'],
    directAliases: ['agy', 'antigravity'],
    canonicalDefault: 'agy',
    // Antigravity encodes the effort in the model id suffix (see `agy models`),
    // so there is no separate effort control. A session/prompt sent during a
    // live turn ends that turn early, so input arriving mid-turn must wait.
    capabilities: {
      modelCatalog: 'dynamic',
      effortControl: 'unsupported',
      activeInput: 'queue-next-turn',
      scopePolicy: 'any',
      outputOwnership: 'runtime',
      sessionAuthority: 'preserve-process',
      agentTools: 'core',
    },
  }),
});

export const RUNTIME_IDS = frozenStrings(Object.keys(RUNTIME_CATALOG));

export const RUNTIME_ALIASES = Object.freeze(Object.fromEntries(
  RUNTIME_IDS.flatMap((id) => RUNTIME_CATALOG[id].aliases.map((alias) => [alias, id])),
));

export function getRuntimeDefinition(runtimeOrAlias) {
  const id = Object.hasOwn(RUNTIME_ALIASES, runtimeOrAlias)
    ? RUNTIME_ALIASES[runtimeOrAlias]
    : runtimeOrAlias;
  return Object.hasOwn(RUNTIME_CATALOG, id) ? RUNTIME_CATALOG[id] : null;
}

// Resolve the selected model before consulting capabilities. Never combine
// efforts from unrelated models (or fall back from an explicit empty list).
export function currentModelEntry({ runtime, model, resolvedModel, models = [] }) {
  const selected = models.find((entry) => entry.id === model);
  if (selected) return selected;
  const resolved = models.find((entry) => entry.id === resolvedModel
    || (entry.resolvedModel && [model, resolvedModel].includes(entry.resolvedModel)));
  if (resolved) return resolved;
  if (resolvedModel) return undefined;
  if (!model || model === getRuntimeDefinition(runtime)?.canonicalDefault) {
    return models.find((entry) => entry.isDefault);
  }
  return undefined;
}

export function supportedModelEfforts(snapshot) {
  return currentModelEntry(snapshot)?.efforts ?? [];
}

// undefined means discovery did not establish capabilities; `null` explicitly
// disables the override for a model that has no effort control. `null` is the
// only spelling of "no override": '' was one too, and it is not a DSH level, so
// persisting it made the next process refuse to construct at all.
export function compatibleModelEffort(entry, effort) {
  if (!entry || entry.effortsKnown === false || !Array.isArray(entry.efforts)) return undefined;
  if (!entry.efforts.length) return null;
  if (entry.efforts.includes(effort)) return effort;
  if (entry.efforts.includes(entry.defaultEffort)) return entry.defaultEffort;
  return entry.efforts.includes('medium') ? 'medium' : entry.efforts[0];
}

export function isModelUnavailableError(error) {
  return classifyModelError(error).kind === 'unknown-model';
}

// Structured model-failure kinds. The coordinator falls back only on
// 'unknown-model'; the other kinds are reported so a gateway that refuses a
// key is never mistaken for a model that does not exist.
export const MODEL_ERROR_KINDS = Object.freeze({
  UNKNOWN_MODEL: 'unknown-model',
  NO_PERMISSION: 'no-permission',
  GATEWAY_ERROR: 'gateway-error',
  TRANSIENT: 'transient',
  EFFORT: 'effort',
});

const MODEL_ERROR_CODES = Object.freeze({
  'MODEL_UNAVAILABLE': MODEL_ERROR_KINDS.UNKNOWN_MODEL,
  'MODEL_NOT_FOUND': MODEL_ERROR_KINDS.UNKNOWN_MODEL,
  'MODEL_UNKNOWN': MODEL_ERROR_KINDS.UNKNOWN_MODEL,
  'UNKNOWN_MODEL': MODEL_ERROR_KINDS.UNKNOWN_MODEL,
  'UNSUPPORTED_REASONING_EFFORT': MODEL_ERROR_KINDS.EFFORT,
  'MISSING_CREDENTIAL': MODEL_ERROR_KINDS.NO_PERMISSION,
  'PERMISSION_DENIED': MODEL_ERROR_KINDS.NO_PERMISSION,
  'PERMISSION_DENIED_ERROR': MODEL_ERROR_KINDS.NO_PERMISSION,
  'FORBIDDEN': MODEL_ERROR_KINDS.NO_PERMISSION,
  'UNAUTHORIZED': MODEL_ERROR_KINDS.NO_PERMISSION,
  'INVALID_API_KEY': MODEL_ERROR_KINDS.NO_PERMISSION,
  'GATEWAY_ERROR': MODEL_ERROR_KINDS.GATEWAY_ERROR,
  'BAD_GATEWAY': MODEL_ERROR_KINDS.GATEWAY_ERROR,
  'UPSTREAM_ERROR': MODEL_ERROR_KINDS.GATEWAY_ERROR,
  'ECONNRESET': MODEL_ERROR_KINDS.TRANSIENT,
  'ECONNREFUSED': MODEL_ERROR_KINDS.TRANSIENT,
  'ETIMEDOUT': MODEL_ERROR_KINDS.TRANSIENT,
  'EAI_AGAIN': MODEL_ERROR_KINDS.TRANSIENT,
  'ENOTFOUND': MODEL_ERROR_KINDS.TRANSIENT,
  'UND_ERR_CONNECT_TIMEOUT': MODEL_ERROR_KINDS.TRANSIENT,
  'DSH_RPC_TIMEOUT': MODEL_ERROR_KINDS.TRANSIENT,
  'DSH_CONNECTION_LOST': MODEL_ERROR_KINDS.TRANSIENT,
});

function httpStatusOf(error) {
  for (const value of [
    error?.status, error?.statusCode, error?.httpStatus,
    error?.response?.status, error?.data?.status, error?.data?.statusCode,
    error?.data?.error?.status, error?.cause?.status,
  ]) {
    const status = Number(value);
    if (Number.isInteger(status) && status >= 100 && status <= 599) return status;
  }
  // A JSON-RPC error carrying an HTTP status string in `code` ("404").
  for (const value of [error?.code, error?.data?.code, error?.data?.error?.code]) {
    if (typeof value === 'string' && /^[1-5]\d\d$/.test(value)) return Number(value);
  }
  return 0;
}

// Only reached when nothing structured identifies the failure. Text matching is
// the fallback the DSH adapter's -32603 initialization error needs, never the
// primary signal, and the negative list keeps an outage or a rejected key from
// reading as a missing model.
const MODEL_UNAVAILABLE_TEXT = /\b(?:unknown|invalid|unsupported|unavailable) model\b|\bmodel\b[^\n]{0,180}\b(?:not found|does not exist|not available|not supported|is unavailable|may not exist)\b/i;
const NOT_MODEL_UNAVAILABLE_TEXT = /\b(?:effort|thinking level|reasoning level|reasoning effort|authentication|unauthorized|forbidden|permission|credentials?|API key|network|connection|timed out|timeout|rate limited|too many requests|service (?:is )?unavailable|bad gateway|gateway)\b/i;

/**
 * Classify a model failure from structured signals first — our own error
 * codes, HTTP statuses on the error or its JSON-RPC data — and from the
 * message only when neither exists.
 * @param {unknown} error - any error object or RPC error.
 * @returns {{kind: string|null, status: number, code: string, retryable: boolean}}
 */
export function classifyModelError(error) {
  if (!error || typeof error !== 'object') {
    return { kind: null, status: 0, code: '', retryable: false };
  }
  const code = String(error.code ?? error.data?.code ?? '').trim();
  const status = httpStatusOf(error);
  const result = (kind) => ({
    kind,
    status,
    code,
    retryable: kind === MODEL_ERROR_KINDS.TRANSIENT || kind === MODEL_ERROR_KINDS.GATEWAY_ERROR,
  });
  if (status === 401 || status === 403) return result(MODEL_ERROR_KINDS.NO_PERMISSION);
  // A gateway answering for the upstream is an outage on the route, not a
  // verdict on the model: report it, retry it, never fall the model back.
  if (status === 502 || status === 503 || status === 504) return result(MODEL_ERROR_KINDS.GATEWAY_ERROR);
  if (status === 408 || status === 425 || status === 429 || status >= 500) return result(MODEL_ERROR_KINDS.TRANSIENT);
  // A 404 is at least as often a wrong path on the route (`POST
  // /v1/chat/completions` against a host that only serves `/v1/messages`) as a
  // missing model, and this classification decides whether ANY runtime silently
  // falls back to a different model. Demand that the answer names a model.
  if (status === 404) {
    return result(MODEL_UNAVAILABLE_TEXT.test(String(error.message ?? ''))
      ? MODEL_ERROR_KINDS.UNKNOWN_MODEL
      : null);
  }
  if (status === 400 || status === 422) {
    // A bad request only names a missing model when it says so; a rejected
    // parameter or an over-long context must not trigger a model fallback.
    return result(MODEL_UNAVAILABLE_TEXT.test(String(error.message ?? '')) ? MODEL_ERROR_KINDS.UNKNOWN_MODEL : null);
  }
  const mapped = MODEL_ERROR_CODES[code];
  if (mapped) return result(mapped);
  const message = String(error.message ?? '');
  if (!message) return result(null);
  // An unstructured -32603 from DSH's initialize names the model but carries no
  // status; the negative list keeps outages and rejected keys out.
  if (NOT_MODEL_UNAVAILABLE_TEXT.test(message)) return result(null);
  if (MODEL_UNAVAILABLE_TEXT.test(message)) return result(MODEL_ERROR_KINDS.UNKNOWN_MODEL);
  return result(null);
}

export function isDirectRuntimeAlias(value) {
  const raw = String(value ?? '');
  return RUNTIME_IDS.some((id) => RUNTIME_CATALOG[id].directAliases.includes(raw));
}

// A runtime whose static models all live on one provider route (dsh and its
// deepseek-official routes) reads a bare model name as a model of that route.
function staticProviderPrefix(definition) {
  const providers = [...new Set(definition.models
    .filter((id) => id.includes('/'))
    .map((id) => id.slice(0, id.indexOf('/'))))];
  return providers.length === 1 ? providers[0] : '';
}

/**
 * The model id a runtime will actually apply. Bare names get the runtime's
 * single catalog provider prefixed; anything else is already canonical.
 * @param {string} runtimeOrAlias - runtime id or alias.
 * @param {string} model - requested model, bare or provider/model.
 * @returns {string} the canonical id, or the input when it cannot be resolved.
 */
export function canonicalRuntimeModel(runtimeOrAlias, model) {
  const definition = getRuntimeDefinition(runtimeOrAlias);
  const raw = String(model ?? '').trim();
  if (!definition || !raw || raw.includes('/')) return raw;
  const provider = staticProviderPrefix(definition);
  return provider ? `${provider}/${raw}` : raw;
}

export function staticModelEntries(runtime = null) {
  const definitions = runtime
    ? [getRuntimeDefinition(runtime)].filter(Boolean)
    : RUNTIME_IDS.map((id) => RUNTIME_CATALOG[id]);
  return definitions.flatMap((definition) => definition.models.map((id) => ({
    id,
    efforts: definition.efforts,
  })));
}

export function parseRuntimeModelSelector(value) {
  const raw = String(value ?? '');
  const qualified = /^([^:]+):(.*)$/.exec(raw);
  const qualifier = qualified && Object.hasOwn(RUNTIME_ALIASES, qualified[1])
    ? qualified[1]
    : null;
  if (qualifier) {
    return {
      raw,
      qualified: true,
      qualifier,
      runtime: RUNTIME_ALIASES[qualifier],
      model: qualified[2],
    };
  }
  const direct = RUNTIME_IDS
    .map((id) => RUNTIME_CATALOG[id])
    .find((definition) => definition.directAliases.includes(raw));
  return {
    raw,
    qualified: false,
    qualifier: null,
    runtime: null,
    model: direct?.defaultModel ?? raw,
  };
}

function staticModelRuntime(model) {
  return RUNTIME_IDS.find((id) => RUNTIME_CATALOG[id].models.includes(model)) ?? null;
}

export function modelMatchesRuntime(model, runtimeOrAlias) {
  const definition = getRuntimeDefinition(runtimeOrAlias);
  if (!definition) return false;
  const owner = staticModelRuntime(model);
  // Legacy configuration inference keeps Claude's known aliases. Interactive
  // model selection instead uses the active runtime's live catalog exclusively.
  if (definition.capabilities.modelCatalog === 'dynamic' && definition.id !== 'claude') {
    return owner === null || owner === definition.id;
  }
  return owner === definition.id;
}

export function resolveRuntimeModelTarget(value, currentRuntime = null) {
  const selector = parseRuntimeModelSelector(value);
  if (selector.qualified) {
    const definition = RUNTIME_CATALOG[selector.runtime];
    const model = canonicalRuntimeModel(definition.id, selector.model || definition.defaultModel);
    return {
      runtime: definition.id,
      model,
      canonical: model || definition.canonicalDefault,
    };
  }

  const direct = RUNTIME_IDS
    .map((id) => RUNTIME_CATALOG[id])
    .find((definition) => definition.directAliases.includes(selector.raw));
  if (direct) {
    return {
      runtime: direct.id,
      model: canonicalRuntimeModel(direct.id, direct.defaultModel),
      canonical: direct.defaultModel || direct.canonicalDefault,
    };
  }

  const owner = staticModelRuntime(selector.model);
  if (owner) {
    const model = canonicalRuntimeModel(owner, selector.model);
    return { runtime: owner, model, canonical: model };
  }
  const current = getRuntimeDefinition(currentRuntime);
  if (current?.capabilities.modelCatalog === 'dynamic' && current.id !== 'claude') {
    const model = canonicalRuntimeModel(current.id, selector.model);
    return {
      runtime: current.id,
      model,
      canonical: model || current.canonicalDefault,
    };
  }
  return { runtime: DEFAULT_RUNTIME_ID, model: selector.model, canonical: selector.model };
}
