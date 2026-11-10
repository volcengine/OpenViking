// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { updateManifest } from './deployment-manifest.mjs';
import { getRuntimeDefinition, resolveRuntimeModelTarget } from './runtime-catalog.mjs';

// The manifest's `model` field predates live runtime switching. Setup writes
// these stable selectors, so /runtime uses the same representation instead of
// leaking a runtime's current provider-specific model into setup's runtime
// choice.
export function runtimeManifestSelector(runtime) {
  const definition = getRuntimeDefinition(runtime);
  if (!definition) throw new Error(`Unknown runtime: ${runtime}`);
  if (definition.directAliases.includes(definition.canonicalDefault)) {
    return definition.canonicalDefault;
  }
  return `${definition.id}:`;
}

// Persist the desired runtime before SQLite commits the live authority handoff.
// The two stores intentionally describe different facts: the manifest is the
// desired runtime, while SQLite is the runtime that has successfully started.
export function updateRuntimePreference(config, runtime) {
  if (!config.manifestPath || !config.instanceId) {
    throw new Error('cannot locate the current instance configuration');
  }
  const previous = config.initialModel || '';
  const target = runtimeManifestSelector(runtime);
  let saved = target;
  updateManifest(config.manifestPath, (value, deployment) => {
    const instance = deployment.agents.find((agent) => agent.id === config.instanceId);
    if (!instance || instance.dataDir !== config.dataDir) {
      throw new Error('instance configuration changed; runtime was not saved');
    }
    const configuredRuntime = resolveRuntimeModelTarget(instance.model).runtime;
    if (instance.model !== previous && configuredRuntime !== runtime) {
      throw new Error('configured runtime changed elsewhere; restart the instance to synchronize it');
    }
    // A repeated /runtime selection must not erase an explicit model selector.
    // When the manifest already names this runtime, it already expresses the
    // desired state and can be left byte-for-byte unchanged.
    saved = configuredRuntime === runtime ? instance.model : target;
    value.agents.find((agent) => agent.id === config.instanceId).model = saved;
  });
  config.initialModel = saved;
  return saved;
}
