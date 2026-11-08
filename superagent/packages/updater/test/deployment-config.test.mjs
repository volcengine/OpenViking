// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
const {
  applyDeploymentAgentEnv,
  deploymentDaemonEnvOverrides,
  applyDeploymentDaemonEnv,
  loadDeploymentManifest,
  resolveDeploymentManifestPath,
  resolveSecretsFilePath,
  resolvedDeploymentDaemonEnv,
  selectDeploymentAgent,
} = require('../../../deployment-config.cjs');

function validManifest() {
  return {
    version: 1,
    defaultInstance: 'beta',
    env: {
      OV_SUPERAGENT_LOG_LEVEL: 'info',
      OV_SUPERAGENT_DRY_RUN: 'false',
      OV_SUPERAGENT_TRAEX_EFFORT: 'medium',
      OV_SUPERAGENT_IGNORED_SENDER_IDS: '',
    },
    agents: [
      {
        id: 'alpha',
        displayName: 'Alpha',
        profile: 'cli_alpha',
        port: 3738,
        dataDir: '/srv/superagent/data/alpha',
        workspace: '/srv/superagent/workspaces/alpha',
        ovConfig: '/srv/superagent/ov/alpha.json',
        model: 'gpt-5.6-sol',
      },
      {
        id: 'beta',
        displayName: 'Beta',
        profile: 'cli_beta',
        port: 3740,
        dataDir: '/srv/superagent/data/beta',
        workspace: '/srv/superagent/workspaces/beta',
        ovConfig: '/srv/superagent/ov/beta.json',
        model: 'gpt-5.6-sol',
      },
      {
        id: 'gamma',
        displayName: 'Gamma',
        profile: 'cli_gamma',
        port: 3741,
        dataDir: '/srv/superagent/data/gamma',
        workspace: '/srv/superagent/workspaces/gamma',
        ovConfig: '/srv/superagent/ov/gamma.json',
        model: 'gpt-5.6-sol',
      },
    ],
  };
}

function withManifest(value, callback) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-deployment-'));
  const manifestPath = path.join(directory, 'config.json');
  fs.writeFileSync(manifestPath, `${JSON.stringify(value, null, 2)}\n`);
  try {
    return callback(manifestPath);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

test('loads any number of agents from one absolute deployment manifest', () => {
  withManifest(validManifest(), (manifestPath) => {
    const deployment = loadDeploymentManifest({
      OV_SUPERAGENT_CONFIG: manifestPath,
    });

    assert.equal(deployment.version, 1);
    assert.equal(deployment.defaultInstance, 'beta');
    assert.deepEqual(deployment.env, {
      OV_SUPERAGENT_LOG_LEVEL: 'info',
      OV_SUPERAGENT_DRY_RUN: 'false',
      OV_SUPERAGENT_TRAEX_EFFORT: 'medium',
      OV_SUPERAGENT_IGNORED_SENDER_IDS: '',
    });
    assert.deepEqual(deployment.agents.map(({ id }) => id), ['alpha', 'beta', 'gamma']);
    assert.equal(selectDeploymentAgent(deployment).id, 'beta');
    assert.equal(selectDeploymentAgent(deployment, 'gamma').profile, 'cli_gamma');
  });
});

test('passes timer storage selection from the manifest into the daemon environment', () => {
  const manifest = validManifest();
  manifest.env.OV_SUPERAGENT_TIMER_STORAGE = 'sqlite';
  withManifest(manifest, (manifestPath) => {
    const deployment = loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath });
    const env = {};
    applyDeploymentDaemonEnv(env, deployment);
    assert.equal(env.OV_SUPERAGENT_TIMER_STORAGE, 'sqlite');
    assert.equal(resolvedDeploymentDaemonEnv(deployment, { OV_SUPERAGENT_TIMER_STORAGE: 'ov' }).OV_SUPERAGENT_TIMER_STORAGE, 'ov');
  });
});

test('defaults the manifest path under HOME and accepts an explicit absolute override', () => {
  assert.equal(
    resolveDeploymentManifestPath({ HOME: '/Users/test' }),
    '/Users/test/.ov-superagent/config.json',
  );
  assert.equal(resolveDeploymentManifestPath({
    HOME: '/Users/test',
    OV_SUPERAGENT_CONFIG: '/srv/lark/config.json',
  }), '/srv/lark/config.json');
  assert.throws(
    () => resolveDeploymentManifestPath({ HOME: 'relative' }),
    /HOME must be an absolute path/,
  );
});

test('passes the temporary directory through deployment and supports isolated instance overrides', () => {
  const manifest = validManifest();
  manifest.env.OV_SUPERAGENT_TEMP_DIR = '/tmp/superagent-default';
  manifest.agents[0].tempDir = '/tmp/superagent-alpha';
  manifest.agents[1].tempDir = '/tmp/superagent-beta';
  withManifest(manifest, (manifestPath) => {
    const sourceEnv = { OV_SUPERAGENT_CONFIG: manifestPath };
    const deployment = loadDeploymentManifest(sourceEnv);
    assert.deepEqual(deployment.ignoredFields, []);
    assert.equal(deploymentDaemonEnvOverrides({ ...sourceEnv, OV_SUPERAGENT_TEMP_DIR: '/tmp/from-shell' }).OV_SUPERAGENT_TEMP_DIR, '/tmp/from-shell');
    for (const [id, expected] of [['alpha', '/tmp/superagent-alpha'], ['beta', '/tmp/superagent-beta'], ['gamma', '/tmp/superagent-default']]) {
      const env = { ...sourceEnv };
      applyDeploymentDaemonEnv(env, deployment);
      applyDeploymentAgentEnv(env, deployment, id);
      assert.equal(env.OV_SUPERAGENT_TEMP_DIR, expected);
      assert.equal(env.OV_SUPERAGENT_DATA_DIR, `/srv/superagent/data/${id}`);
    }
    assert.equal(resolvedDeploymentDaemonEnv(deployment, { OV_SUPERAGENT_TEMP_DIR: '/tmp/from-shell' }).OV_SUPERAGENT_TEMP_DIR, '/tmp/from-shell');
  });
  manifest.agents[1].tempDir = '/tmp/superagent-alpha';
  withManifest(manifest, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), /storage paths overlap/);
  });
  manifest.agents[1].tempDir = 'relative/tmp';
  withManifest(manifest, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), /tempDir must be an absolute path/);
  });
});

test('system environment overrides manifest daemon env while agent identity stays pinned', () => {
  withManifest(validManifest(), (manifestPath) => {
    const env = {
      HOME: '/Users/test',
      OV_SUPERAGENT_CONFIG: manifestPath,
      OV_SUPERAGENT_LOG_LEVEL: 'debug',
      ARK_API_KEY: 'system-only-secret',
      OV_SUPERAGENT_PORT: '9999',
      OV_SUPERAGENT_LARK_PROFILE: 'wrong',
    };
    const deployment = loadDeploymentManifest(env);
    applyDeploymentDaemonEnv(env, deployment);
    applyDeploymentAgentEnv(env, deployment, 'alpha');

    assert.equal(env.OV_SUPERAGENT_LOG_LEVEL, 'debug');
    assert.equal(env.OV_SUPERAGENT_DRY_RUN, 'false');
    assert.equal(env.OV_SUPERAGENT_IGNORED_SENDER_IDS, '');
    assert.equal(env.ARK_API_KEY, 'system-only-secret');
    assert.equal(env.OV_SUPERAGENT_PORT, '3738');
    assert.equal(env.OV_SUPERAGENT_LARK_PROFILE, 'cli_alpha');
  });
});

test('deployment manifest accepts the timer occurrence check delay env', () => {
  const manifest = validManifest();
  manifest.env.OV_SUPERAGENT_TIMER_OCCURRENCE_CHECK_DELAY_MS = '120000';
  withManifest(manifest, (manifestPath) => {
    const deployment = loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath });
    assert.deepEqual(deployment.ignoredFields, []);
    assert.equal(deployment.env.OV_SUPERAGENT_TIMER_OCCURRENCE_CHECK_DELAY_MS, '120000');
    const env = { OV_SUPERAGENT_CONFIG: manifestPath };
    applyDeploymentDaemonEnv(env, deployment);
    assert.equal(env.OV_SUPERAGENT_TIMER_OCCURRENCE_CHECK_DELAY_MS, '120000');
  });
});

test('falls back to a secrets file for daemon-wide secrets missing from the environment', () => {
  withManifest(validManifest(), (manifestPath) => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-secrets-'));
    const secretsPath = path.join(directory, 'secrets.env');
    fs.writeFileSync(secretsPath, '# comment\nARK_API_KEY=from-file-secret\nOTHER=ignored\n');
    try {
      const env = {
        HOME: '/Users/test',
        OV_SUPERAGENT_CONFIG: manifestPath,
        OV_SUPERAGENT_SECRETS_FILE: secretsPath,
      };
      const deployment = loadDeploymentManifest(env);
      applyDeploymentDaemonEnv(env, deployment);
      assert.equal(env.ARK_API_KEY, 'from-file-secret');
      assert.equal(deploymentDaemonEnvOverrides(env).ARK_API_KEY, 'from-file-secret');
      assert.equal(env.OTHER, undefined);
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
});

test('the secrets file can carry a durable runtime provider key', () => {
  withManifest(validManifest(), (manifestPath) => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-secrets-'));
    const secretsPath = path.join(directory, 'secrets.env');
    fs.writeFileSync(secretsPath, 'OPENAI_API_KEY=sk-test\nANTHROPIC_API_KEY=ak-test\n');
    try {
      const env = {
        HOME: '/Users/test',
        OV_SUPERAGENT_CONFIG: manifestPath,
        OV_SUPERAGENT_SECRETS_FILE: secretsPath,
      };
      const deployment = loadDeploymentManifest(env);
      applyDeploymentDaemonEnv(env, deployment);
      assert.equal(env.OPENAI_API_KEY, 'sk-test');
      assert.equal(env.ANTHROPIC_API_KEY, 'ak-test');
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
});

test('the environment still wins over the secrets file', () => {
  withManifest(validManifest(), (manifestPath) => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-secrets-'));
    const secretsPath = path.join(directory, 'secrets.env');
    fs.writeFileSync(secretsPath, 'ARK_API_KEY=from-file-secret\n');
    try {
      const env = {
        HOME: '/Users/test',
        OV_SUPERAGENT_CONFIG: manifestPath,
        OV_SUPERAGENT_SECRETS_FILE: secretsPath,
        ARK_API_KEY: 'from-shell-env',
      };
      const deployment = loadDeploymentManifest(env);
      applyDeploymentDaemonEnv(env, deployment);
      assert.equal(env.ARK_API_KEY, 'from-shell-env');
    } finally {
      fs.rmSync(directory, { recursive: true, force: true });
    }
  });
});

test('a missing secrets file is not an error', () => {
  withManifest(validManifest(), (manifestPath) => {
    const env = {
      HOME: '/Users/test',
      OV_SUPERAGENT_CONFIG: manifestPath,
      OV_SUPERAGENT_SECRETS_FILE: '/no/such/file/secrets.env',
    };
    const deployment = loadDeploymentManifest(env);
    applyDeploymentDaemonEnv(env, deployment);
    assert.equal(env.ARK_API_KEY, undefined);
  });
});

test('manifest env passes on only the daemon-wide allowlist and drops every other key', () => {
  const dropped = [
    ['reserved identity', 'OV_SUPERAGENT_LARK_PROFILE', 'cli_wrong'],
    ['reserved control plane', 'OV_SUPERAGENT_UPDATER_SOCKET', '/tmp/wrong'],
    ['system-only secret', 'ARK_API_KEY', 'must-not-live-in-manifest'],
    ['removed Codex model', 'OV_SUPERAGENT_CODEX_MODEL', 'gpt-5.6-sol'],
    ['retired history cap', 'OV_SUPERAGENT_COGNITION_MAX_CHARS', '120000'],
    ['unknown key', 'UNRELATED_SETTING', 'value'],
  ];
  for (const [name, key, value] of dropped) {
    const manifest = validManifest();
    manifest.env[key] = value;
    withManifest(manifest, (manifestPath) => {
      const deployment = loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath });
      assert.deepEqual(deployment.ignoredFields, [`env.${key}`], name);
      assert.equal(Object.hasOwn(deployment.env, key), false, name);
      const processEnv = resolvedDeploymentDaemonEnv(deployment, { OV_SUPERAGENT_SECRETS_FILE: '/no/such/file/secrets.env' });
      assert.equal(Object.hasOwn(processEnv, key), false, name);
    });
  }
  const manifest = validManifest();
  manifest.env.OV_SUPERAGENT_HISTORY_LIMIT = 30;
  withManifest(manifest, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), /env\.OV_SUPERAGENT_HISTORY_LIMIT must be a string/);
  });
});

test('ignores and reports unknown manifest fields at every level', () => {
  const manifest = validManifest();
  manifest.instances = [];
  manifest.agents[0].alias = 'old-name';
  manifest.agents[0].meetingAudio = { pythonCommand: '/usr/bin/python3' };
  manifest.agents[1].connectors = ['lark', 'email'];
  manifest.agents[1].emailAccounts = [{ id: 'email-gmail', config: '/srv/superagent/secrets/beta-gmail.json', label: 'Gmail' }];
  manifest.env.OV_SUPERAGENT_COGNITION_MAX_CHARS = '120000';
  withManifest(manifest, (manifestPath) => {
    const deployment = loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath });
    assert.deepEqual(deployment.ignoredFields, [
      'instances',
      'env.OV_SUPERAGENT_COGNITION_MAX_CHARS',
      'agents[0].alias',
      'agents[0].meetingAudio',
      'agents[1].emailAccounts[0].label',
    ]);
    const alpha = selectDeploymentAgent(deployment, 'alpha');
    assert.equal(Object.hasOwn(alpha, 'alias'), false);
    assert.deepEqual(selectDeploymentAgent(deployment, 'beta').emailAccounts, [
      { id: 'email-gmail', config: '/srv/superagent/secrets/beta-gmail.json' },
    ]);
  });
  withManifest(validManifest(), (manifestPath) => {
    assert.deepEqual(loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }).ignoredFields, []);
  });
});

test('foreground selection defaults to defaultInstance and pins every identity boundary', () => {
  withManifest(validManifest(), (manifestPath) => {
    const env = {
      OV_SUPERAGENT_CONFIG: manifestPath,
      OV_SUPERAGENT_VOLC_SESSION_ID: 'legacy-global-seed',
      OV_SUPERAGENT_VOLC_ENVIRONMENT_ID: 'legacy-global-environment',
    };
    const deployment = loadDeploymentManifest(env);
    applyDeploymentAgentEnv(env, deployment);

    assert.deepEqual({
      id: env.OV_SUPERAGENT_INSTANCE_ID,
      target: env.OV_SUPERAGENT_UPGRADE_TARGET,
      name: env.OV_SUPERAGENT_AGENT_NAME,
      profile: env.OV_SUPERAGENT_LARK_PROFILE,
      cliProfile: env.LARKSUITE_CLI_PROFILE,
      port: env.OV_SUPERAGENT_PORT,
      dataDir: env.OV_SUPERAGENT_DATA_DIR,
      workspace: env.OV_SUPERAGENT_WORKSPACE,
      ovConfig: env.OPENVIKING_CLI_CONFIG_FILE,
      model: env.OV_SUPERAGENT_MODEL,
    }, {
      id: 'beta',
      target: 'beta',
      name: 'Beta',
      profile: 'cli_beta',
      cliProfile: 'cli_beta',
      port: '3740',
      dataDir: '/srv/superagent/data/beta',
      workspace: '/srv/superagent/workspaces/beta',
      ovConfig: '/srv/superagent/ov/beta.json',
      model: 'gpt-5.6-sol',
    });
    assert.equal(env.OV_SUPERAGENT_CODEX_MODEL, undefined);
    assert.equal(env.OV_SUPERAGENT_VOLC_SESSION_ID, undefined);
    assert.equal(env.OV_SUPERAGENT_VOLC_ENVIRONMENT_ID, undefined);
  });
});

test('pins Ark as a generic initial model without misconfiguring Codex', () => {
  const manifest = validManifest();
  manifest.agents[1].model = 'ark';
  manifest.agents[1].volcSessionId = 'sesn-beta-self-hosted';
  manifest.agents[1].volcEnvironmentId = 'env-beta-self-hosted';
  withManifest(manifest, (manifestPath) => {
    const env = { OV_SUPERAGENT_CONFIG: manifestPath };
    const deployment = loadDeploymentManifest(env);
    applyDeploymentAgentEnv(env, deployment);
    assert.equal(env.OV_SUPERAGENT_MODEL, 'ark');
    assert.equal(env.OV_SUPERAGENT_CODEX_MODEL, undefined);
    assert.equal(env.OV_SUPERAGENT_VOLC_SESSION_ID, 'sesn-beta-self-hosted');
    assert.equal(env.OV_SUPERAGENT_VOLC_ENVIRONMENT_ID, 'env-beta-self-hosted');
  });
});

test('requires a complete per-agent self-hosted binding for Ark', () => {
  const cases = [
    [
      'missing binding',
      (agent) => { agent.model = 'ark'; },
      /using ark must configure volcSessionId and volcEnvironmentId/,
    ],
    [
      'partial binding',
      (agent) => { agent.volcSessionId = 'sesn-partial'; },
      /volcSessionId and agents\[1\]\.volcEnvironmentId must be configured together/,
    ],
  ];
  for (const [name, mutate, error] of cases) {
    const manifest = validManifest();
    mutate(manifest.agents[1]);
    withManifest(manifest, (manifestPath) => {
      assert.throws(
        () => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }),
        error,
        name,
      );
    });
  }
});

test('connects mailboxes only together with the email connector, each account file once', () => {
  const manifest = validManifest();
  const mailboxes = [{ id: 'email-gmail', config: '/srv/superagent/secrets/beta-gmail.json' }, { id: 'email-qq', config: '/srv/superagent/secrets/beta-qq.json' }];
  manifest.agents[1].connectors = ['email', 'lark'];
  manifest.agents[1].emailAccounts = mailboxes;
  withManifest(manifest, (manifestPath) => {
    const env = { OV_SUPERAGENT_CONFIG: manifestPath, OV_SUPERAGENT_EMAIL_ACCOUNTS_JSON: '[]' };
    const deployment = loadDeploymentManifest(env);
    // A mailbox is never the primary IM, wherever it is listed.
    assert.equal(selectDeploymentAgent(deployment).channel, 'lark');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.OV_SUPERAGENT_CONNECTORS_JSON, '["email","lark"]');
    assert.deepEqual(JSON.parse(env.OV_SUPERAGENT_EMAIL_ACCOUNTS_JSON), mailboxes);
    applyDeploymentAgentEnv(env, deployment, 'gamma');
    assert.equal(env.OV_SUPERAGENT_EMAIL_ACCOUNTS_JSON, undefined);
  });
  for (const [mutate, error] of [
    [(agent) => { agent.connectors = ['lark', 'email']; }, /emailAccounts and the email connector must be configured together/],
    [(agent) => { agent.emailAccounts = mailboxes; }, /emailAccounts and the email connector must be configured together/],
    [(agent) => { agent.connectors = ['lark', 'email']; agent.emailAccounts = [mailboxes[0], { ...mailboxes[1], id: 'email-gmail' }]; }, /two mailboxes called email-gmail/],
    [(agent) => { agent.connectors = ['lark', 'email']; agent.emailAccounts = [{ ...mailboxes[0], id: 'Gmail' }]; }, /must be email-<name>/],
    [(agent) => { agent.connectors = ['lark', 'email']; agent.emailAccounts = [mailboxes[0], { ...mailboxes[1], config: mailboxes[0].config }]; }, /duplicate email account file/],
  ]) {
    const broken = validManifest();
    mutate(broken.agents[1]);
    withManifest(broken, (manifestPath) => {
      assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), error);
    });
  }
});


test('OV memory is the daemon\'s; per-line search is opt-in; any OV plugin that loads stays inert; no Lark profile for Weixin', () => {
  const manifest = validManifest();
  manifest.agents[1].ovAutoRecall = true;
  // Retired options: ignored, never an error.
  manifest.agents[1].ovRecallScope = 'actor';
  manifest.agents[1].ovRemember = true;
  manifest.agents[2].channel = 'weixin';
  manifest.agents[2].connectors = ['weixin'];
  delete manifest.agents[2].profile;
  withManifest(manifest, (manifestPath) => {
    const env = {
      OV_SUPERAGENT_CONFIG: manifestPath,
      OPENVIKING_CLI_CONFIG_FILE: '/stale/ovcli.conf',
      OV_SUPERAGENT_LARK_PROFILE: 'stale',
      LARKSUITE_CLI_PROFILE: 'stale',
    };
    const deployment = loadDeploymentManifest(env);
    assert.equal(selectDeploymentAgent(deployment, 'alpha').ovAutoRecall, false);
    applyDeploymentAgentEnv(env, deployment, 'alpha');
    assert.equal(env.OPENVIKING_CLI_CONFIG_FILE, '/srv/superagent/ov/alpha.json');
    assert.equal(env.OPENVIKING_MEMORY_ENABLED, '0');
    assert.equal(env.OPENVIKING_AUTO_RECALL, '0');
    assert.equal(env.OV_SUPERAGENT_OV_AUTO_RECALL, 'false');
    assert.equal(env.OV_SUPERAGENT_OV_RECALL_SCOPE, undefined);
    assert.equal(env.OV_SUPERAGENT_OV_REMEMBER, undefined);
    // OpenViking SuperAgent owns OV: a plugin that loads anyway writes and recalls nothing.
    assert.equal(env.OPENVIKING_AUTO_CAPTURE, '0');
    assert.equal(env.OPENVIKING_AUTO_COMMIT_ON_COMPACT, '0');
    assert.equal(env.OPENVIKING_TAKEOVER, '0');
    assert.equal(env.OPENVIKING_WORKSPACE_PEER, '0');
    assert.equal(env.OPENVIKING_RECALL_PEER_SCOPE, 'all');
    // Plugin state and queued writes stay inside this instance.
    assert.equal(env.OPENVIKING_HOME, '/srv/superagent/data/alpha/openviking');
    assert.equal(env.OPENVIKING_STATE_DIR, '/srv/superagent/data/alpha/openviking/state');
    assert.equal(env.OPENVIKING_HOOK_STATE_DIR, '/srv/superagent/data/alpha/openviking/hook-state');
    assert.equal(env.OPENVIKING_CODEX_STATE_DIR, '/srv/superagent/data/alpha/openviking/codex-plugin-state');
    assert.equal(env.OPENVIKING_PENDING_DIR, '/srv/superagent/data/alpha/openviking/pending');
    assert.equal(env.OPENVIKING_RECALL_LEDGER_DIR, '/srv/superagent/data/alpha/openviking/recall-ledger');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.OPENVIKING_CLI_CONFIG_FILE, '/srv/superagent/ov/beta.json');
    assert.equal(env.OPENVIKING_MEMORY_ENABLED, '0');
    // Search on: the daemon searches (ov-recall.mjs); the plugins' own stays off.
    assert.equal(env.OPENVIKING_AUTO_RECALL, '0');
    assert.equal(env.OV_SUPERAGENT_OV_AUTO_RECALL, 'true');
    assert.equal(selectDeploymentAgent(deployment, 'beta').ovRecallScope, undefined);
    assert.equal(env.OPENVIKING_PENDING_DIR, '/srv/superagent/data/beta/openviking/pending');
    assert.equal(env.LARKSUITE_CLI_PROFILE, 'cli_beta');
    applyDeploymentAgentEnv(env, deployment, 'gamma');
    assert.equal(selectDeploymentAgent(deployment, 'gamma').profile, null);
    assert.equal(env.OV_SUPERAGENT_LARK_PROFILE, undefined);
    assert.equal(env.LARKSUITE_CLI_PROFILE, undefined);
  });
});

test('explicit silence is on by default except on Ark, an explicit false opts out, and it is always pinned in the daemon env', () => {
  const manifest = validManifest();
  delete manifest.agents[0].explicitSilence;
  manifest.agents[1].explicitSilence = false;
  withManifest(manifest, (manifestPath) => {
    const env = { OV_SUPERAGENT_CONFIG: manifestPath, OV_SUPERAGENT_EXPLICIT_SILENCE: 'false' };
    const deployment = loadDeploymentManifest(env);
    assert.notEqual(selectDeploymentAgent(deployment, 'alpha').model, 'ark');
    assert.equal(selectDeploymentAgent(deployment, 'alpha').explicitSilence, true);
    assert.equal(selectDeploymentAgent(deployment, 'beta').explicitSilence, false);
    applyDeploymentAgentEnv(env, deployment, 'alpha');
    assert.equal(env.OV_SUPERAGENT_EXPLICIT_SILENCE, 'true');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.OV_SUPERAGENT_EXPLICIT_SILENCE, 'false');
  });
  // Ark has no Core tools to offer stay_silent through: off unless set.
  const ark = validManifest();
  Object.assign(ark.agents[0], { model: 'ark', volcSessionId: 'sess', volcEnvironmentId: 'env' });
  delete ark.agents[0].explicitSilence;
  withManifest(ark, (manifestPath) => {
    const env = { OV_SUPERAGENT_CONFIG: manifestPath };
    const deployment = loadDeploymentManifest(env);
    assert.equal(selectDeploymentAgent(deployment, 'alpha').explicitSilence, false);
    applyDeploymentAgentEnv(env, deployment, 'alpha');
    assert.equal(env.OV_SUPERAGENT_EXPLICIT_SILENCE, 'false');
  });
  const broken = validManifest();
  broken.agents[0].explicitSilence = 'true';
  withManifest(broken, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), /agents\[0\]\.explicitSilence must be a boolean/);
  });
});

test('the scheduler is legacy unless an instance picks the inbox, and is always pinned in the daemon env', () => {
  const manifest = validManifest();
  manifest.agents[1].scheduler = 'inbox';
  withManifest(manifest, (manifestPath) => {
    const env = { VIKA_CONFIG: manifestPath, VIKA_SCHEDULER: 'inbox' };
    const deployment = loadDeploymentManifest(env);
    assert.equal(selectDeploymentAgent(deployment, 'alpha').scheduler, 'legacy');
    applyDeploymentAgentEnv(env, deployment, 'alpha');
    assert.equal(env.VIKA_SCHEDULER, 'legacy');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.VIKA_SCHEDULER, 'inbox');
  });
  const broken = validManifest();
  broken.agents[0].scheduler = 'parallel';
  withManifest(broken, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }), /agents\[0\]\.scheduler must be legacy or inbox or workers/);
  });
});

test('staged replies are opt-in per instance and always pinned in the daemon env', () => {
  const manifest = validManifest();
  manifest.agents[1].stagedReplies = true;
  withManifest(manifest, (manifestPath) => {
    const env = { OV_SUPERAGENT_CONFIG: manifestPath, OV_SUPERAGENT_STAGED_REPLIES: 'true' };
    const deployment = loadDeploymentManifest(env);
    assert.equal(selectDeploymentAgent(deployment, 'alpha').stagedReplies, false);
    applyDeploymentAgentEnv(env, deployment, 'alpha');
    assert.equal(env.OV_SUPERAGENT_STAGED_REPLIES, 'false');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.OV_SUPERAGENT_STAGED_REPLIES, 'true');
  });
  const broken = validManifest();
  broken.agents[0].stagedReplies = 1;
  withManifest(broken, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), /agents\[0\]\.stagedReplies must be a boolean/);
  });
});

test('Lark message access is the owner\'s unless an instance keeps to the bot\'s chats, and only a Lark instance has it', () => {
  const manifest = validManifest();
  manifest.agents[1].larkMessageAccess = 'bot';
  manifest.agents[2].channel = 'weixin';
  manifest.agents[2].connectors = ['weixin'];
  delete manifest.agents[2].profile;
  withManifest(manifest, (manifestPath) => {
    const env = { OV_SUPERAGENT_CONFIG: manifestPath, OV_SUPERAGENT_LARK_MESSAGE_ACCESS: 'bot' };
    const deployment = loadDeploymentManifest(env);
    assert.equal(selectDeploymentAgent(deployment, 'alpha').larkMessageAccess, 'owner');
    applyDeploymentAgentEnv(env, deployment, 'alpha');
    assert.equal(env.OV_SUPERAGENT_LARK_MESSAGE_ACCESS, 'owner');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.OV_SUPERAGENT_LARK_MESSAGE_ACCESS, 'bot');
    applyDeploymentAgentEnv(env, deployment, 'gamma');
    assert.equal(selectDeploymentAgent(deployment, 'gamma').larkMessageAccess, null);
    assert.equal(env.OV_SUPERAGENT_LARK_MESSAGE_ACCESS, undefined);
  });
  const wrong = validManifest();
  wrong.agents[0].larkMessageAccess = 'everyone';
  withManifest(wrong, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), /agents\[0\]\.larkMessageAccess must be owner or bot/);
  });
  const elsewhere = validManifest();
  elsewhere.agents[2].channel = 'weixin';
  elsewhere.agents[2].connectors = ['weixin'];
  delete elsewhere.agents[2].profile;
  elsewhere.agents[2].larkMessageAccess = 'bot';
  withManifest(elsewhere, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), /agents\[2\]\.larkMessageAccess is only used by the lark connector/);
  });
});

test('meeting notes are opt-in for one Feishu/Lark instance at most, and always pinned in the daemon env', () => {
  const manifest = validManifest();
  manifest.agents[1].meetingNotes = true;
  withManifest(manifest, (manifestPath) => {
    const env = { OV_SUPERAGENT_CONFIG: manifestPath, OV_SUPERAGENT_MEETING_NOTES: 'true' };
    const deployment = loadDeploymentManifest(env);
    assert.equal(selectDeploymentAgent(deployment, 'alpha').meetingNotes, false);
    applyDeploymentAgentEnv(env, deployment, 'alpha');
    assert.equal(env.OV_SUPERAGENT_MEETING_NOTES, 'false');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.OV_SUPERAGENT_MEETING_NOTES, 'true');
  });
  const two = validManifest();
  two.agents[0].meetingNotes = true;
  two.agents[2].meetingNotes = true;
  withManifest(two, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), /only one instance can store meeting notes; alpha and gamma both do/);
  });
  const weixin = validManifest();
  Object.assign(weixin.agents[1], { channel: 'weixin', connectors: ['weixin'], meetingNotes: true });
  delete weixin.agents[1].profile;
  withManifest(weixin, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), /agents\[1\]\.meetingNotes requires the lark connector/);
  });
  const broken = validManifest();
  broken.agents[0].meetingNotes = 'yes';
  withManifest(broken, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), /agents\[0\]\.meetingNotes must be a boolean/);
  });
});

test('an instance names a shipped role or its own role file, and keeps natural writing unless it turns it off', () => {
  const manifest = validManifest();
  manifest.agents[0].role = 'assistant';
  manifest.agents[1].role = '/srv/superagent/roles/beta.md';
  manifest.agents[1].naturalWriting = false;
  withManifest(manifest, (manifestPath) => {
    const env = { OV_SUPERAGENT_CONFIG: manifestPath, OV_SUPERAGENT_ROLE: 'qa', OV_SUPERAGENT_NATURAL_WRITING: 'false' };
    const deployment = loadDeploymentManifest(env);
    assert.equal(selectDeploymentAgent(deployment, 'alpha').naturalWriting, true);
    applyDeploymentAgentEnv(env, deployment, 'alpha');
    assert.equal(env.OV_SUPERAGENT_ROLE, 'assistant');
    assert.equal(env.OV_SUPERAGENT_NATURAL_WRITING, 'true');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.OV_SUPERAGENT_ROLE, '/srv/superagent/roles/beta.md');
    assert.equal(env.OV_SUPERAGENT_NATURAL_WRITING, 'false');
    // No role: neither the shell's nor another instance's leaks in.
    applyDeploymentAgentEnv(env, deployment, 'gamma');
    assert.equal(env.OV_SUPERAGENT_ROLE, undefined);
  });
  for (const [field, value, message] of [
    ['role', 'pirate', /agents\[0\]\.role must be colleague, assistant, qa or an absolute path/],
    ['role', 'roles/mine.md', /agents\[0\]\.role must be colleague, assistant, qa or an absolute path/],
    ['naturalWriting', 'no', /agents\[0\]\.naturalWriting must be a boolean/],
  ]) {
    const broken = validManifest();
    broken.agents[0][field] = value;
    withManifest(broken, (manifestPath) => {
      assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), message);
    });
  }
});

test('rejects missing and invalid manifest fields instead of guessing', () => {
  const cases = [
    {
      name: 'OV configuration is required',
      mutate: (manifest) => { delete manifest.agents[0].ovConfig; },
      error: /agents\[0\]\.ovConfig is required/,
    },
    {
      name: 'recall must be a JSON boolean',
      mutate: (manifest) => { manifest.agents[0].ovAutoRecall = 'true'; },
      error: /agents\[0\]\.ovAutoRecall must be a boolean/,
    },
    {
      name: 'missing daemon env',
      mutate: (manifest) => { delete manifest.env; },
      error: /deployment manifest env must be an object/,
    },
    {
      name: 'missing agent field',
      mutate: (manifest) => { delete manifest.agents[0].profile; },
      error: /agents\[0\]\.profile is required/,
    },
    {
      name: 'profile on an instance without the lark connector',
      mutate: (manifest) => { manifest.agents[1].channel = 'weixin'; manifest.agents[1].connectors = ['weixin']; },
      error: /agents\[1\]\.profile is only used by the lark connector/,
    },
    {
      name: 'display name too long for Web routing',
      mutate: (manifest) => { manifest.agents[0].displayName = 'x'.repeat(41); },
      error: /agents\[0\]\.displayName is invalid/,
    },
  ];

  for (const fixture of cases) {
    const manifest = validManifest();
    fixture.mutate(manifest);
    withManifest(manifest, (manifestPath) => {
      assert.throws(
        () => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }),
        fixture.error,
        fixture.name,
      );
    });
  }
});

test('an instance id cannot take the name of a shared control-plane app', () => {
  for (const id of ['all', 'web', 'updater']) {
    const manifest = validManifest();
    manifest.agents[1].id = id;
    manifest.defaultInstance = 'alpha';
    withManifest(manifest, (manifestPath) => {
      assert.throws(() => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }), /cannot be all, web, updater/, id);
    });
  }
});

test('rejects duplicate agent identities, endpoints, and storage boundaries', () => {
  const cases = [
    ['id', (manifest) => { manifest.agents[1].id = 'alpha'; }, /duplicate agent id: alpha/],
    ['profile', (manifest) => { manifest.agents[1].profile = 'cli_alpha'; }, /duplicate agent profile: cli_alpha/],
    ['port', (manifest) => { manifest.agents[1].port = 3738; }, /duplicate agent port: 3738/],
    [
      'dataDir',
      (manifest) => { manifest.agents[1].dataDir = '/srv/superagent/data/alpha'; },
      /duplicate agent dataDir: \/srv\/superagent\/data\/alpha/,
    ],
    [
      'workspace',
      (manifest) => { manifest.agents[1].workspace = '/srv/superagent/workspaces/alpha'; },
      /duplicate agent workspace: \/srv\/superagent\/workspaces\/alpha/,
    ],
    [
      'ovConfig',
      (manifest) => { manifest.agents[1].ovConfig = '/srv/superagent/ov/alpha.json'; },
      /duplicate agent ovConfig: \/srv\/superagent\/ov\/alpha\.json/,
    ],
  ];

  for (const [name, mutate, error] of cases) {
    const manifest = validManifest();
    mutate(manifest);
    withManifest(manifest, (manifestPath) => {
      assert.throws(
        () => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }),
        error,
        name,
      );
    });
  }
});

test('rejects invalid defaults, relative paths, and overlapping agent storage', () => {
  const cases = [
    [
      'missing default',
      (manifest) => { manifest.defaultInstance = 'missing'; },
      /deployment instance is not configured: missing/,
    ],
    [
      'relative dataDir',
      (manifest) => { manifest.agents[0].dataDir = 'data/alpha'; },
      /agents\[0\]\.dataDir must be an absolute path/,
    ],
    [
      'cross-agent overlap',
      (manifest) => { manifest.agents[1].workspace = '/srv/superagent/data/alpha/nested'; },
      /agent storage paths overlap: agents\[0\]\.dataDir and agents\[1\]\.workspace/,
    ],
    [
      'same-agent overlap',
      (manifest) => { manifest.agents[0].workspace = '/srv/superagent/data/alpha/work'; },
      /agent storage paths overlap: agents\[0\]\.dataDir and agents\[0\]\.workspace/,
    ],
  ];

  for (const [name, mutate, error] of cases) {
    const manifest = validManifest();
    mutate(manifest);
    withManifest(manifest, (manifestPath) => {
      assert.throws(
        () => loadDeploymentManifest({ OV_SUPERAGENT_CONFIG: manifestPath }),
        error,
        name,
      );
    });
  }
});


test('a legacy pre-release home is neither an error nor read: the default is always ~/.ov-superagent', (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'superagent-legacy-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));
  for (const name of ['.vika', '.ovsa', '.superagent']) {
    fs.mkdirSync(path.join(home, name));
    fs.writeFileSync(path.join(home, name, 'config.json'), '{}');
    fs.writeFileSync(path.join(home, name, 'secrets.env'), 'OV_SUPERAGENT_TEST=legacy');
  }
  assert.equal(resolveDeploymentManifestPath({ HOME: home }), path.join(home, '.ov-superagent', 'config.json'));
  assert.equal(resolveSecretsFilePath({ HOME: home }), path.join(home, '.ov-superagent', 'secrets.env'));
  const config = path.join(home, '.vika', 'config.json');
  assert.equal(resolveDeploymentManifestPath({ HOME: home, OV_SUPERAGENT_CONFIG: config }), config);
  assert.equal(fs.readFileSync(config, 'utf8'), '{}', 'legacy data is left alone');
});
