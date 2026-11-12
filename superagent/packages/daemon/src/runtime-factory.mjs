// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { CodexAgent } from './codex.mjs';
import { TraexAgent } from './traex.mjs';
import { ClaudeAgent } from './claude.mjs';
import { VolcAgent } from './volc.mjs';
import { PiAgent } from './pi.mjs';
import { DurableAgent } from './durable.mjs';
import { OpencodeAgent } from './opencode.mjs';
import { AgyAgent } from './agy.mjs';
import { DshAgent } from './dsh.mjs';
import { RUNTIME_IDS, getRuntimeDefinition } from './runtime-catalog.mjs';

// Composition root for transport implementations. The coordinator depends on
// factories and the adapter contract, never on provider classes.
export function createRuntimeFactories(config, store, logger, overrides = {}) {
  const definitions = RUNTIME_IDS.map(getRuntimeDefinition);
  const constructors = {
    codex: CodexAgent,
    traex: TraexAgent,
    claude: ClaudeAgent,
    volc: VolcAgent,
    pi: PiAgent,
    durable: DurableAgent,
    opencode: OpencodeAgent,
    agy: AgyAgent,
    dsh: DshAgent,
  };
  return Object.fromEntries(definitions.map((definition) => {
    const override = overrides[`${definition.id}Factory`];
    if (override) return [definition.id, override];
    return [definition.id, (backendOptions) => {
      const Agent = constructors[definition.id];
      return new Agent(
        config,
        store,
        logger.child({ component: definition.id }),
        backendOptions,
      );
    }];
  }));
}
