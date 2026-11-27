// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import path from 'node:path';
import { describe, it } from 'node:test';
import { RUNTIME_IDS } from '../src/runtime-catalog.mjs';
import { availableRuntimeIds, runtimeAvailability } from '../src/runtime-availability.mjs';

// The bundled runtimes are absolute paths inside the install (Pi's bundle entry,
// Durable's host script), so the stub matches them by file name; the rest are bare
// commands resolved on PATH, as the platform launcher does.
const BUNDLED = ['cli.js', 'durable-host.mjs'];

function machine({ commands = ['dsh'], bundled = BUNDLED, key = '', env = {} } = {}) {
  return {
    env: { PATH: '/usr/bin', ...(key ? { ARK_API_KEY: key } : {}), ...env },
    findExecutable: (name) => (commands.includes(name) ? `/opt/bin/${name}` : null),
    exists: (file) => bundled.includes(path.basename(file)),
  };
}

describe('runtime availability', () => {
  it('offers the runtimes that ship with the release on a machine with no agent CLIs', () => {
    const availability = runtimeAvailability({}, machine());

    assert.deepEqual(availableRuntimeIds({}, machine()), ['pi', 'durable', 'dsh']);
    // Everything else is in the catalogue but not on this machine, with the reason the
    // card groups it under.
    for (const id of ['codex', 'traex', 'claude', 'opencode', 'agy']) {
      assert.deepEqual(availability[id], { available: false, reason: 'command' }, id);
    }
    // Ark is remote: the question is the credential, not a command.
    assert.deepEqual(availability.volc, { available: false, reason: 'key' });
    // Every runtime in the catalogue is answered for, so a caller never has to guess.
    assert.deepEqual(Object.keys(availability), [...RUNTIME_IDS]);
  });

  it('counts a runtime whose command is installed but whose login is missing', () => {
    // Signing in is the runtime's own business: it starts and reports that itself, so
    // the dropdown keeps offering it.
    const availability = runtimeAvailability({}, machine({ commands: ['dsh', 'codex', 'claude'] }));
    assert.equal(availability.codex.available, true);
    assert.equal(availability.claude.available, true);
    assert.equal(availability.traex.available, false);
  });

  it('offers Ark once its key is configured', () => {
    assert.deepEqual(runtimeAvailability({ volcApiKey: 'sk-ark' }, machine()).volc, { available: true, reason: '' });
    assert.equal(runtimeAvailability({}, machine({ key: 'sk-ark' })).volc.available, true);
  });

  it('uses the configured command, and does not trust one that is not there', () => {
    // A *_BIN the operator pinned is what the daemon spawns, so it decides.
    assert.equal(runtimeAvailability({ codexBin: 'codex-nightly' }, machine({ commands: ['codex-nightly'] })).codex.available, true);
    assert.equal(runtimeAvailability({ codexBin: 'codex-nightly' }, machine({ commands: ['codex'] })).codex.available, false);
    const absolute = path.join(path.sep, 'opt', 'missing', 'codex');
    assert.equal(runtimeAvailability({ codexBin: absolute }, machine({ commands: ['codex'] })).codex.available, false);
  });

  it('answers unavailable for a bundled runtime the install cannot resolve', () => {
    // A tree without its dependencies resolves neither Pi's entry point nor Durable's
    // host script; the probe says so instead of failing the whole card.
    const availability = runtimeAvailability({}, machine({ bundled: [] }));
    assert.equal(availability.pi.available, false);
    assert.deepEqual(availability.durable, { available: false, reason: 'command' });
  });

  it('falls back to the catalogue defaults when the configuration says nothing', () => {
    // No bins configured at all: a bare command name per runtime, as config.mjs sets.
    const availability = runtimeAvailability({}, machine({ commands: ['codex', 'dsh'] }));
    assert.equal(availability.codex.available, true);
    assert.equal(availability.dsh.available, true);
  });
});
