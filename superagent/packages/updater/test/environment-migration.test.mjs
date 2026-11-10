// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import test from 'node:test';
import { sanitizeAgentChildEnv, sanitizeNonArkChildEnv } from '../../daemon/src/process-utils.mjs';
import { cleanWebPm2ControlEnv } from '../src/pm2.mjs';
const require = createRequire(import.meta.url);
const { normalizeEnv } = require('../../../env-compat.cjs');
const { resolveDeploymentManifestPath, deploymentDaemonEnvOverrides } = require('../../../deployment-config.cjs');

test('canonical environment values win over both legacy namespaces, including empty values', () => {
  const old = { VIKA_CONFIG: '/old.json', OVSA_CONFIG: '/intermediate.json', OV_SUPERAGENT_CONFIG: '', VIKA_LOG_LEVEL: 'info', OVSA_LOG_LEVEL: 'debug', VIKA_DOWNLOAD_BASE: 'https://example.test/legacy', KEEP: 'yes' };
  assert.deepEqual(normalizeEnv(old), { OV_SUPERAGENT_CONFIG: '', OV_SUPERAGENT_LOG_LEVEL: 'debug', OV_SUPERAGENT_DOWNLOAD_BASE: 'https://example.test/legacy', KEEP: 'yes' });
  assert.equal(old.VIKA_CONFIG, '/old.json');
  assert.equal(resolveDeploymentManifestPath({ VIKA_CONFIG: '/legacy/config.json' }), '/legacy/config.json');
  assert.equal(deploymentDaemonEnvOverrides({ VIKA_LOG_LEVEL: 'debug', HOME: '/nonexistent-home' }).OV_SUPERAGENT_LOG_LEVEL, 'debug');
});

test('credential boundaries strip canonical and legacy runtime variables', () => {
  const source = { OV_SUPERAGENT_VOLC_TOKEN: 'secret', VIKA_VOLC_TOKEN: 'secret', OVSA_VOLC_TOKEN: 'secret', OV_SUPERAGENT_AUTH_TOKEN: 'secret', VIKA_AUTH_TOKEN: 'secret', OVSA_AUTH_TOKEN: 'secret', PATH: '/usr/bin' };
  assert.deepEqual(sanitizeAgentChildEnv(source, { cliBins: [] }), { PATH: '/usr/bin', FORCE_COLOR: '0', NO_COLOR: '1' });
  for (const clean of [sanitizeNonArkChildEnv(source), cleanWebPm2ControlEnv(source)]) {
    for (const prefix of ['OV_SUPERAGENT', 'VIKA', 'OVSA']) assert.equal(clean[`${prefix}_VOLC_TOKEN`], undefined);
  }
});
