// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// The DSH configuration surface, end to end: what the daemon reads for DSH
// (config.mjs) and how those variables travel through the deployment manifest
// and the pm2/updater environments (deployment-config.cjs). DSH owns its model
// configuration, so the only daemon-side DSH settings are the binary path and
// the start-up reasoning effort.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { afterEach, describe, it } from 'node:test';
import { getConfig } from '../src/config.mjs';

const require = createRequire(import.meta.url);
const {
  DAEMON_ENV_FIELDS,
  DAEMON_SECRET_ENV_FIELDS,
  DAEMON_SHELL_ENV_FIELDS,
  applyDeploymentDaemonEnv,
  deploymentDaemonEnvOverrides,
  loadDeploymentManifest,
} = require('../../../deployment-config.cjs');

const DSH_EFFORTS = ['off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

const saved = new Map();

function setEnv(name, value) {
  if (!saved.has(name)) saved.set(name, process.env[name]);
  if (value === undefined) delete process.env[name];
  else process.env[name] = value;
}

afterEach(() => {
  for (const [name, value] of saved) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
  saved.clear();
});

function validManifest() {
  return {
    version: 1,
    defaultInstance: 'alpha',
    env: { OV_SUPERAGENT_LOG_LEVEL: 'info' },
    agents: [
      {
        id: 'alpha',
        displayName: 'Alpha',
        profile: 'cli_alpha',
        port: 3738,
        dataDir: '/srv/vika/data/alpha',
        workspace: '/srv/vika/workspaces/alpha',
        ovConfig: '/srv/vika/ov/alpha.json',
        model: 'gpt-5.6-sol',
      },
      {
        id: 'beta',
        displayName: 'Beta',
        profile: 'cli_beta',
        port: 3740,
        dataDir: '/srv/vika/data/beta',
        workspace: '/srv/vika/workspaces/beta',
        ovConfig: '/srv/vika/ov/beta.json',
        model: 'gpt-5.6-sol',
      },
    ],
  };
}

function withManifest(value, callback) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-dsh-config-'));
  const manifestPath = path.join(directory, 'config.json');
  fs.writeFileSync(manifestPath, `${JSON.stringify(value, null, 2)}\n`);
  try {
    return callback(manifestPath);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

describe('the daemon reads DSH from the environment only', { concurrency: false }, () => {
  it('takes the DSH binary path from DSH_BIN and falls back to the PATH lookup', () => {
    setEnv('DSH_BIN', undefined);
    assert.equal(getConfig().dshBin, 'dsh');
    setEnv('DSH_BIN', '/opt/dsh/bin/dsh');
    assert.equal(getConfig().dshBin, '/opt/dsh/bin/dsh');
  });

  it('accepts every reasoning level DSH understands and defaults to high', () => {
    for (const effort of DSH_EFFORTS) {
      setEnv('OV_SUPERAGENT_DSH_EFFORT', effort);
      assert.equal(getConfig().dshEffort, effort);
    }
    setEnv('OV_SUPERAGENT_DSH_EFFORT', undefined);
    assert.equal(getConfig().dshEffort, 'high');
    setEnv('OV_SUPERAGENT_DSH_EFFORT', 'turbo');
    assert.throws(() => getConfig(), /OV_SUPERAGENT_DSH_EFFORT must be one of: off, minimal, low, medium, high, xhigh, max/);
    setEnv('OV_SUPERAGENT_DSH_EFFORT', '');
    assert.equal(getConfig().dshEffort, 'high', 'an empty value falls back to the default, not to an error');
  });

  it('bounds a start at 30s by default and lets the operator widen it', () => {
    // A start that exceeds the bound fails the daemon, and a source-tree dsh is
    // two `node --import tsx` launches: fast when the machine is idle, seconds
    // when it is not.
    setEnv('OV_SUPERAGENT_DSH_START_TIMEOUT_MS', undefined);
    assert.equal(getConfig().dshStartTimeoutMs, 30_000);
    setEnv('OV_SUPERAGENT_DSH_START_TIMEOUT_MS', '120000');
    assert.equal(getConfig().dshStartTimeoutMs, 120_000);
    setEnv('OV_SUPERAGENT_DSH_START_TIMEOUT_MS', '0');
    assert.throws(() => getConfig(), /OV_SUPERAGENT_DSH_START_TIMEOUT_MS/);
  });

  it('has no DSH model seed: the route lives in DSH\'s own settings document', () => {
    // Neither variable was renamed: a stale environment setting must not
    // resurrect a second model configuration source outside DSH.
    setEnv('OV_SUPERAGENT_DSH_MODEL', 'deepseek-official/deepseek-v4-pro');
    setEnv('OV_SUPERAGENT_DSH_MODELS_FILE', '/opt/models/models.json');
    const config = getConfig();
    assert.equal(config.dshModel, undefined);
    assert.equal(config.dshModelsFile, undefined);
    assert.equal(Object.hasOwn(config, 'dshModel'), false);
    assert.equal(Object.hasOwn(config, 'dshModelsFile'), false);
    assert.equal(config.dshEffort, 'high', 'the model variables cannot change the effort default');
  });
});

describe('how the DSH variables travel through deployment', () => {
  it('puts the binary in the deployment fields and the effort in shell-only fields', () => {
    assert.ok(DAEMON_ENV_FIELDS.includes('DSH_BIN'), 'DSH_BIN is a deployment fact like every *_BIN');
    assert.equal(DAEMON_ENV_FIELDS.includes('OV_SUPERAGENT_DSH_EFFORT'), false,
      'OV_SUPERAGENT_DSH_EFFORT must not be a manifest field');
    assert.deepEqual(DAEMON_SHELL_ENV_FIELDS, ['OV_SUPERAGENT_DSH_EFFORT']);
    // Neither is a secret that only the secret file may carry.
    for (const name of ['DSH_BIN', 'OV_SUPERAGENT_DSH_EFFORT']) {
      assert.equal(DAEMON_SECRET_ENV_FIELDS.includes(name), false, `${name} must not be a secret field`);
    }
  });

  it('takes DSH_BIN from the manifest but refuses the effort there', () => {
    const manifest = validManifest();
    Object.assign(manifest.env, { DSH_BIN: '/opt/dsh', OV_SUPERAGENT_DSH_EFFORT: 'xhigh' });
    withManifest(manifest, (manifestPath) => {
      const deployment = loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath });
      assert.deepEqual([...deployment.ignoredFields], ['env.OV_SUPERAGENT_DSH_EFFORT']);
      assert.equal(deployment.env.DSH_BIN, '/opt/dsh');
      assert.equal(deployment.env.OV_SUPERAGENT_DSH_EFFORT, undefined);
      const env = {};
      applyDeploymentDaemonEnv(env, deployment);
      assert.equal(env.DSH_BIN, '/opt/dsh');
      assert.equal(env.OV_SUPERAGENT_DSH_EFFORT, undefined, 'the manifest is not a source for the effort');
    });
  });

  it('forwards the DSH effort from the operator environment only', () => {
    const manifest = validManifest();
    withManifest(manifest, (manifestPath) => {
      const sourceEnv = { OV_SUPERAGENT_CONFIG: manifestPath, DSH_BIN: '/opt/dsh-from-shell', OV_SUPERAGENT_DSH_EFFORT: 'xhigh' };
      const deployment = loadDeploymentManifest(sourceEnv);
      assert.deepEqual(deployment.ignoredFields, []);
      assert.equal(deployment.env.DSH_BIN, undefined, 'the shell is not written into the manifest');
      const env = {};
      applyDeploymentDaemonEnv(env, deployment, sourceEnv);
      assert.equal(env.DSH_BIN, '/opt/dsh-from-shell');
      assert.equal(env.OV_SUPERAGENT_DSH_EFFORT, 'xhigh');
      // The same rule for the updater's own process environment.
      const overrides = deploymentDaemonEnvOverrides(sourceEnv);
      assert.equal(overrides.DSH_BIN, '/opt/dsh-from-shell');
      assert.equal(overrides.OV_SUPERAGENT_DSH_EFFORT, 'xhigh');
    });
  });

  it('drops a retired DSH models file instead of passing it on', () => {
    const manifest = validManifest();
    manifest.env.OV_SUPERAGENT_DSH_MODELS_FILE = '/srv/vika/models.json';
    withManifest(manifest, (manifestPath) => {
      const deployment = loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath });
      const env = {};
      applyDeploymentDaemonEnv(env, deployment);
      assert.equal(env.OV_SUPERAGENT_DSH_MODELS_FILE, undefined);
      assert.ok(deployment.ignoredFields.includes('env.OV_SUPERAGENT_DSH_MODELS_FILE'), deployment.ignoredFields.join(', '));
    });
  });
});
