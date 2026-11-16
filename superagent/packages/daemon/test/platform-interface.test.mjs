// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd. and the OpenViking contributors.
// SPDX-License-Identifier: AGPL-3.0
import test from 'node:test';
import assert from 'node:assert/strict';
import * as facade from '../src/platform/index.mjs';
import * as posix from '../src/platform/posix.mjs';
import * as windows from '../src/platform/win32.mjs';
import * as android from '../src/platform/android.mjs';
import { capabilitiesFor } from '../src/capabilities.mjs';

test('all OS implementations and the facade implement exactly the declared interface', () => {
  const expected = [...facade.interfaceKeys].sort();
  assert.equal(new Set(expected).size, expected.length);
  for (const implementation of [posix, windows, android]) {
    assert.deepEqual(Object.keys(implementation).sort(), expected);
    for (const key of expected.filter(key => !['loginService', 'maxCommandLineLength'].includes(key))) {
      assert.equal(typeof implementation[key], typeof posix[key], key);
    }
    assert.ok([null, 'launchd', 'systemd', 'runit', 'startupFolder'].includes(implementation.loginService));
    assert.ok(implementation.maxCommandLineLength === null || Number.isSafeInteger(implementation.maxCommandLineLength));
  }
  assert.deepEqual(Object.keys(facade).filter(key => key !== 'interfaceKeys').sort(), expected);
  const selected = process.platform === 'win32' ? windows : process.platform === 'android' ? android : posix;
  for (const key of expected) assert.equal(facade[key], selected[key], key);
});

test('preview product support has one immutable policy, independent of adapter selection', () => {
  assert.deepEqual(capabilitiesFor({ ...posix, hostPlatformId: 'linux-x64' }), { updater: true, autostart: true, connectors: { email: true } });
  assert.deepEqual(capabilitiesFor(windows), { updater: false, autostart: true, connectors: { email: true } });
  const unsupported = capabilitiesFor({ ...windows, hostPlatformId: 'win32-arm64' });
  assert.equal(unsupported.updater, false);
  assert.ok(Object.isFrozen(unsupported));
  assert.ok(Object.isFrozen(unsupported.connectors));
  assert.deepEqual(capabilitiesFor(android), { updater: false, autostart: true, connectors: { email: true } });
  assert.equal(capabilitiesFor({ ...posix, hostPlatformId: 'unknown-arm64' }).updater, false);
});

test('file mode enforcement is a platform property: posix enforces it, win32 does not', () => {
  assert.equal(posix.supportsFileModes, true);
  assert.equal(windows.supportsFileModes, false);
});
