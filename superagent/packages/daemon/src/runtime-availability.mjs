// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Which runtimes this machine can start at all.
//
// runtime-catalog.mjs answers what OpenViking SuperAgent supports, and it is the same
// list on every machine. This answers the other question the Runtime dropdown asks:
// whether the runtime is here. The card used to offer every id in the catalogue, so an
// install running on the runtimes it ships with showed codex, traex, claude, volc,
// opencode and agy as if they were choices: picking one interrupted the running turn
// and then failed to start.
//
// A local runtime is available when the command the daemon would spawn resolves, using
// the same PATH lookup the spawn uses (the daemon's own environment, fixed at start).
// Ark runs remotely and needs its key instead. Being signed in is a separate question
// this does not answer: a CLI that is installed but logged out still starts, and says
// so in its own words.

import fs from 'node:fs';
import path from 'node:path';

import { RUNTIME_IDS } from './runtime-catalog.mjs';
import { findExecutable } from './platform/index.mjs';
import { managedPiBin } from './pi-runtime.mjs';
import { durableHostPath } from './durable-runtime.mjs';

// Why a runtime is not offered. `command`: the daemon cannot find what it spawns.
// `key`: a remote runtime without the credential it needs.
export const UNAVAILABLE_REASONS = Object.freeze({
  COMMAND: 'command',
  KEY: 'key',
});

// The command the daemon spawns for one runtime, or '' where it spawns none.
function spawnCommand(id, config) {
  try {
    if (id === 'durable') return durableHostPath(config.durableHostPath ?? '');
    if (id === 'pi') return config.piBin || managedPiBin();
    return config[`${id}Bin`] || id;
  } catch {
    // The bundled runtime's entry point could not be resolved at all (an install
    // without its dependencies), which is exactly "not available here".
    return '';
  }
}

function resolves(command, { env, find, exists }) {
  const value = String(command ?? '');
  if (!value) return false;
  // A path (absolute, or carrying a separator) is what the spawn gets verbatim;
  // a bare name is looked up on PATH, as the platform launcher does.
  if (path.isAbsolute(value) || value.includes('/') || value.includes(path.sep)) {
    return exists(path.resolve(value));
  }
  return Boolean(find(value, env));
}

const defaultExists = (file) => {
  try { return fs.statSync(file).isFile(); } catch { return false; }
};

/**
 * Whether each runtime in the catalogue can start on this machine.
 * @param {object} [config] - the daemon configuration (the `*Bin` fields, `durableHostPath`, `volcApiKey`).
 * @param {object} [options] - injected environment and lookups, for tests.
 * @param {object} [options.env] - the environment to resolve bare commands in.
 * @param {Function} [options.findExecutable] - PATH lookup.
 * @param {Function} [options.exists] - path existence check.
 * @returns {Record<string, {available: boolean, reason: string}>} one entry per runtime id, in catalogue order.
 */
export function runtimeAvailability(config = {}, options = {}) {
  const probe = {
    env: options.env ?? process.env,
    find: options.findExecutable ?? findExecutable,
    exists: options.exists ?? defaultExists,
  };
  return Object.fromEntries(RUNTIME_IDS.map((id) => {
    if (id === 'volc') {
      const keyed = Boolean(String(config.volcApiKey ?? probe.env.ARK_API_KEY ?? '').trim());
      return [id, { available: keyed, reason: keyed ? '' : UNAVAILABLE_REASONS.KEY }];
    }
    const available = resolves(spawnCommand(id, config), probe);
    return [id, { available, reason: available ? '' : UNAVAILABLE_REASONS.COMMAND }];
  }));
}

/**
 * The runtime ids that can start here, in catalogue order.
 * @param {object} [config] - as for runtimeAvailability.
 * @param {object} [options] - as for runtimeAvailability.
 * @returns {string[]}
 */
export function availableRuntimeIds(config = {}, options = {}) {
  const availability = runtimeAvailability(config, options);
  return RUNTIME_IDS.filter((id) => availability[id].available);
}
