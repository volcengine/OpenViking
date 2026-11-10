// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
// The manifest `env` block DSH depends on. Kept out of the upstream
// manifest.test.mjs: it measures `detectionEnv`, the merge that lets a pinned
// `DSH_BIN` reach runtime detection the way the daemon already uses it.

import assert from 'node:assert/strict';
import test from 'node:test';
import { detectionEnv } from '../src/manifest.mjs';

test('detection sees the manifest env, and the operator environment still wins', () => {
  // A binary path pinned in the manifest is what the daemon runs. Probing only
  // process.env made setup report "not installed" for a runtime the daemon
  // starts every day, so detection merges the two with the daemon's precedence.
  const shell = { HOME: '/Users/test', PATH: '/usr/bin', CODEX_BIN: '/from/shell/codex' };
  const manifest = { env: { DSH_BIN: '/from/manifest/dsh', CODEX_BIN: '/from/manifest/codex' } };

  const merged = detectionEnv(shell, manifest);
  assert.equal(merged.DSH_BIN, '/from/manifest/dsh', 'a manifest-only path reaches detection');
  assert.equal(merged.CODEX_BIN, '/from/shell/codex', 'the operator environment overrides the manifest');
  assert.equal(merged.HOME, '/Users/test');

  // No manifest yet (first run) or a manifest without env: unchanged.
  assert.deepEqual(detectionEnv(shell, null), shell);
  assert.deepEqual(detectionEnv(shell, { env: {} }), shell);
});
