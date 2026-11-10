// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import { createRequire } from 'node:module';
import { wireDshBridge } from './dsh-bridge/protocol.mjs';

const { realpathSync } = fs;

// This module is loaded by DSH, not by OpenViking SuperAgent. Dependencies supplied by the SDK
// server are loaded through DSH's own resolver; OpenViking SuperAgent's node_modules must not be
// a runtime dependency of the published DSH installation.
const BRIDGE_ID = 'vika-dsh-bridge';

export async function loadDshBridgeDependencies({ loader, serverPath = '@deepseek-ai/dsh-sdk-jsonrpc-server' } = {}) {
  let modules;
  if (loader?.internal?.import) {
    modules = await Promise.all([
      loader.internal.import(serverPath, import.meta.url, {}),
      loader.internal.import('@deepseek-ai/dsh-llm', import.meta.url, {}),
    ]);
  } else {
    // npm DSH 0.2.2 does not install the loader's optional internals package.
    // The SDK bundle has a public manifest and directly depends on the server;
    // resolving through that manifest never borrows OpenViking SuperAgent's dependencies.
    let bundleRequire;
    try {
      // process.argv[1] is DSH's entry point, not OpenViking SuperAgent's: the DSH CLI invokes
      // the composed SDK profile in its own process. Anchor package resolution
      // there, then follow its public SDK bundle dependency.
      //
      // Resolve the entry through its real path first. An npm global install
      // runs `<prefix>/bin/dsh`, a symlink into `lib/node_modules/@deepseek-ai/
      // dsh/lib/bin.js`; Node leaves argv[1] as the symlink, so anchoring there
      // looks for the bundle beside the symlink and never finds it. A source
      // checkout has no such hop and resolves the same either way.
      const entry = realpathSync(process.argv[1]);
      const dshRequire = createRequire(entry);
      bundleRequire = createRequire(dshRequire.resolve('@deepseek-ai/dsh-sdk-app/package.json'));
    } catch (error) {
      throw new Error(`${BRIDGE_ID} could not locate DSH's SDK bundle: ${error.message}`);
    }
    modules = await Promise.all([
      import(bundleRequire.resolve(serverPath)),
      import(bundleRequire.resolve('@deepseek-ai/dsh-llm')),
    ]);
  }
  const [stock, llm] = modules;
  if (typeof stock?.apply !== 'function' || typeof llm?.createUserMessage !== 'function') {
    throw new Error(`${BRIDGE_ID} could not load the DSH SDK server and message API`);
  }
  return { stock, createUserMessage: llm.createUserMessage };
}

// Load these before declaring the descriptor: DSH resolves the two package
// imports in this module's context, while the stock server still runs in the
// application's SDK context. This works both for source and npm DSH installs.
const dependencies = await loadDshBridgeDependencies();
export const name = BRIDGE_ID;
export const inject = dependencies.stock.inject;
export const Config = dependencies.stock.Config;

export function apply(ctx, config) {
  wireDshBridge({ ctx, ...dependencies, config });
}

export {
  createBridgeHandlers, DshBridgeFramer, MAX_FRAME_BYTES, wireDshBridge,
} from './dsh-bridge/protocol.mjs';
