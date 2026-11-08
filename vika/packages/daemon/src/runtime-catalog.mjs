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

// undefined means discovery did not establish capabilities; '' explicitly
// disables the override for a model that has no effort control.
export function compatibleModelEffort(entry, effort) {
  if (!entry || entry.effortsKnown === false || !Array.isArray(entry.efforts)) return undefined;
  if (!entry.efforts.length) return '';
  if (entry.efforts.includes(effort)) return effort;
  if (entry.efforts.includes(entry.defaultEffort)) return entry.defaultEffort;
  return entry.efforts.includes('medium') ? 'medium' : entry.efforts[0];
}

export function isModelUnavailableError(error) {
  if (error?.code === 'MODEL_UNAVAILABLE') return true;
  const message = error?.message ?? '';
  if (/\b(?:effort|thinking level|reasoning level|authentication|unauthorized|credentials|API key|network|connection|timed out|timeout|service (?:is )?unavailable)\b/i.test(message)) return false;
  return /\b(?:unknown|invalid|unsupported|unavailable) model\b|\bmodel\b[^\n]{0,180}\b(?:not found|does not exist|not available|not supported|is unavailable|may not exist)\b/i.test(message);
}

export function isDirectRuntimeAlias(value) {
  const raw = String(value ?? '');
  return RUNTIME_IDS.some((id) => RUNTIME_CATALOG[id].directAliases.includes(raw));
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
    const model = selector.model || definition.defaultModel;
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
      model: direct.defaultModel,
      canonical: direct.defaultModel || direct.canonicalDefault,
    };
  }

  const owner = staticModelRuntime(selector.model);
  if (owner) {
    return { runtime: owner, model: selector.model, canonical: selector.model };
  }
  const current = getRuntimeDefinition(currentRuntime);
  if (current?.capabilities.modelCatalog === 'dynamic' && current.id !== 'claude') {
    return {
      runtime: current.id,
      model: selector.model,
      canonical: selector.model || current.canonicalDefault,
    };
  }
  return { runtime: DEFAULT_RUNTIME_ID, model: selector.model, canonical: selector.model };
}
