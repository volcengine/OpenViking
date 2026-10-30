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
  resolvedDeploymentDaemonEnv,
  selectDeploymentAgent,
} = require('../../../deployment-config.cjs');

function validManifest() {
  return {
    version: 1,
    defaultInstance: 'beta',
    env: {
      VIKA_LOG_LEVEL: 'info',
      VIKA_DRY_RUN: 'false',
      VIKA_TRAEX_EFFORT: 'medium',
      VIKA_IGNORED_SENDER_IDS: '',
    },
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
      {
        id: 'gamma',
        displayName: 'Gamma',
        profile: 'cli_gamma',
        port: 3741,
        dataDir: '/srv/vika/data/gamma',
        workspace: '/srv/vika/workspaces/gamma',
        ovConfig: '/srv/vika/ov/gamma.json',
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
      VIKA_CONFIG: manifestPath,
    });

    assert.equal(deployment.version, 1);
    assert.equal(deployment.defaultInstance, 'beta');
    assert.deepEqual(deployment.env, {
      VIKA_LOG_LEVEL: 'info',
      VIKA_DRY_RUN: 'false',
      VIKA_TRAEX_EFFORT: 'medium',
      VIKA_IGNORED_SENDER_IDS: '',
    });
    assert.deepEqual(deployment.agents.map(({ id }) => id), ['alpha', 'beta', 'gamma']);
    assert.equal(selectDeploymentAgent(deployment).id, 'beta');
    assert.equal(selectDeploymentAgent(deployment, 'gamma').profile, 'cli_gamma');
  });
});

test('defaults the manifest path under HOME and accepts an explicit absolute override', () => {
  assert.equal(
    resolveDeploymentManifestPath({ HOME: '/Users/test' }),
    '/Users/test/.vika/config.json',
  );
  assert.equal(resolveDeploymentManifestPath({
    HOME: '/Users/test',
    VIKA_CONFIG: '/srv/lark/config.json',
  }), '/srv/lark/config.json');
  assert.throws(
    () => resolveDeploymentManifestPath({ HOME: 'relative' }),
    /HOME must be an absolute path/,
  );
});

test('passes the temporary directory through deployment and supports isolated instance overrides', () => {
  const manifest = validManifest();
  manifest.env.VIKA_TEMP_DIR = '/tmp/vika-default';
  manifest.agents[0].tempDir = '/tmp/vika-alpha';
  manifest.agents[1].tempDir = '/tmp/vika-beta';
  withManifest(manifest, (manifestPath) => {
    const sourceEnv = { VIKA_CONFIG: manifestPath };
    const deployment = loadDeploymentManifest(sourceEnv);
    assert.deepEqual(deployment.ignoredFields, []);
    assert.equal(deploymentDaemonEnvOverrides({ ...sourceEnv, VIKA_TEMP_DIR: '/tmp/from-shell' }).VIKA_TEMP_DIR, '/tmp/from-shell');
    for (const [id, expected] of [['alpha', '/tmp/vika-alpha'], ['beta', '/tmp/vika-beta'], ['gamma', '/tmp/vika-default']]) {
      const env = { ...sourceEnv };
      applyDeploymentDaemonEnv(env, deployment);
      applyDeploymentAgentEnv(env, deployment, id);
      assert.equal(env.VIKA_TEMP_DIR, expected);
      assert.equal(env.VIKA_DATA_DIR, `/srv/vika/data/${id}`);
    }
    assert.equal(resolvedDeploymentDaemonEnv(deployment, { VIKA_TEMP_DIR: '/tmp/from-shell' }).VIKA_TEMP_DIR, '/tmp/from-shell');
  });
  manifest.agents[1].tempDir = '/tmp/vika-alpha';
  withManifest(manifest, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }), /storage paths overlap/);
  });
  manifest.agents[1].tempDir = 'relative/tmp';
  withManifest(manifest, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }), /tempDir must be an absolute path/);
  });
});

test('system environment overrides manifest daemon env while agent identity stays pinned', () => {
  withManifest(validManifest(), (manifestPath) => {
    const env = {
      HOME: '/Users/test',
      VIKA_CONFIG: manifestPath,
      VIKA_LOG_LEVEL: 'debug',
      ARK_API_KEY: 'system-only-secret',
      VIKA_PORT: '9999',
      VIKA_LARK_PROFILE: 'wrong',
    };
    const deployment = loadDeploymentManifest(env);
    applyDeploymentDaemonEnv(env, deployment);
    applyDeploymentAgentEnv(env, deployment, 'alpha');

    assert.equal(env.VIKA_LOG_LEVEL, 'debug');
    assert.equal(env.VIKA_DRY_RUN, 'false');
    assert.equal(env.VIKA_IGNORED_SENDER_IDS, '');
    assert.equal(env.ARK_API_KEY, 'system-only-secret');
    assert.equal(env.VIKA_PORT, '3738');
    assert.equal(env.VIKA_LARK_PROFILE, 'cli_alpha');
  });
});

test('deployment manifest accepts the timer occurrence check delay env', () => {
  const manifest = validManifest();
  manifest.env.VIKA_TIMER_OCCURRENCE_CHECK_DELAY_MS = '120000';
  withManifest(manifest, (manifestPath) => {
    const deployment = loadDeploymentManifest({ VIKA_CONFIG: manifestPath });
    assert.deepEqual(deployment.ignoredFields, []);
    assert.equal(deployment.env.VIKA_TIMER_OCCURRENCE_CHECK_DELAY_MS, '120000');
    const env = { VIKA_CONFIG: manifestPath };
    applyDeploymentDaemonEnv(env, deployment);
    assert.equal(env.VIKA_TIMER_OCCURRENCE_CHECK_DELAY_MS, '120000');
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
        VIKA_CONFIG: manifestPath,
        VIKA_SECRETS_FILE: secretsPath,
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

test('the environment still wins over the secrets file', () => {
  withManifest(validManifest(), (manifestPath) => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-secrets-'));
    const secretsPath = path.join(directory, 'secrets.env');
    fs.writeFileSync(secretsPath, 'ARK_API_KEY=from-file-secret\n');
    try {
      const env = {
        HOME: '/Users/test',
        VIKA_CONFIG: manifestPath,
        VIKA_SECRETS_FILE: secretsPath,
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
      VIKA_CONFIG: manifestPath,
      VIKA_SECRETS_FILE: '/no/such/file/secrets.env',
    };
    const deployment = loadDeploymentManifest(env);
    applyDeploymentDaemonEnv(env, deployment);
    assert.equal(env.ARK_API_KEY, undefined);
  });
});

test('manifest env passes on only the daemon-wide allowlist and drops every other key', () => {
  const dropped = [
    ['reserved identity', 'VIKA_LARK_PROFILE', 'cli_wrong'],
    ['reserved control plane', 'VIKA_UPDATER_SOCKET', '/tmp/wrong'],
    ['system-only secret', 'ARK_API_KEY', 'must-not-live-in-manifest'],
    ['removed Codex model', 'VIKA_CODEX_MODEL', 'gpt-5.6-sol'],
    ['retired history cap', 'VIKA_COGNITION_MAX_CHARS', '120000'],
    ['unknown key', 'UNRELATED_SETTING', 'value'],
  ];
  for (const [name, key, value] of dropped) {
    const manifest = validManifest();
    manifest.env[key] = value;
    withManifest(manifest, (manifestPath) => {
      const deployment = loadDeploymentManifest({ VIKA_CONFIG: manifestPath });
      assert.deepEqual(deployment.ignoredFields, [`env.${key}`], name);
      assert.equal(Object.hasOwn(deployment.env, key), false, name);
      const processEnv = resolvedDeploymentDaemonEnv(deployment, { VIKA_SECRETS_FILE: '/no/such/file/secrets.env' });
      assert.equal(Object.hasOwn(processEnv, key), false, name);
    });
  }
  const manifest = validManifest();
  manifest.env.VIKA_HISTORY_LIMIT = 30;
  withManifest(manifest, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }), /env\.VIKA_HISTORY_LIMIT must be a string/);
  });
});

test('ignores and reports unknown manifest fields at every level', () => {
  const manifest = validManifest();
  manifest.instances = [];
  manifest.agents[0].alias = 'old-name';
  manifest.agents[0].meetingAudio = { pythonCommand: '/usr/bin/python3' };
  manifest.agents[1].connectors = ['lark', 'email'];
  manifest.agents[1].emailAccounts = [{ id: 'email-gmail', config: '/srv/vika/secrets/beta-gmail.json', label: 'Gmail' }];
  manifest.env.VIKA_COGNITION_MAX_CHARS = '120000';
  withManifest(manifest, (manifestPath) => {
    const deployment = loadDeploymentManifest({ VIKA_CONFIG: manifestPath });
    assert.deepEqual(deployment.ignoredFields, [
      'instances',
      'env.VIKA_COGNITION_MAX_CHARS',
      'agents[0].alias',
      'agents[0].meetingAudio',
      'agents[1].emailAccounts[0].label',
    ]);
    const alpha = selectDeploymentAgent(deployment, 'alpha');
    assert.equal(Object.hasOwn(alpha, 'alias'), false);
    assert.deepEqual(selectDeploymentAgent(deployment, 'beta').emailAccounts, [
      { id: 'email-gmail', config: '/srv/vika/secrets/beta-gmail.json' },
    ]);
  });
  withManifest(validManifest(), (manifestPath) => {
    assert.deepEqual(loadDeploymentManifest({ VIKA_CONFIG: manifestPath }).ignoredFields, []);
  });
});

test('foreground selection defaults to defaultInstance and pins every identity boundary', () => {
  withManifest(validManifest(), (manifestPath) => {
    const env = {
      VIKA_CONFIG: manifestPath,
      VIKA_VOLC_SESSION_ID: 'legacy-global-seed',
      VIKA_VOLC_ENVIRONMENT_ID: 'legacy-global-environment',
    };
    const deployment = loadDeploymentManifest(env);
    applyDeploymentAgentEnv(env, deployment);

    assert.deepEqual({
      id: env.VIKA_INSTANCE_ID,
      target: env.VIKA_UPGRADE_TARGET,
      name: env.VIKA_AGENT_NAME,
      profile: env.VIKA_LARK_PROFILE,
      cliProfile: env.LARKSUITE_CLI_PROFILE,
      port: env.VIKA_PORT,
      dataDir: env.VIKA_DATA_DIR,
      workspace: env.VIKA_WORKSPACE,
      ovConfig: env.OPENVIKING_CLI_CONFIG_FILE,
      model: env.VIKA_MODEL,
    }, {
      id: 'beta',
      target: 'beta',
      name: 'Beta',
      profile: 'cli_beta',
      cliProfile: 'cli_beta',
      port: '3740',
      dataDir: '/srv/vika/data/beta',
      workspace: '/srv/vika/workspaces/beta',
      ovConfig: '/srv/vika/ov/beta.json',
      model: 'gpt-5.6-sol',
    });
    assert.equal(env.VIKA_CODEX_MODEL, undefined);
    assert.equal(env.VIKA_VOLC_SESSION_ID, undefined);
    assert.equal(env.VIKA_VOLC_ENVIRONMENT_ID, undefined);
  });
});

test('pins Ark as a generic initial model without misconfiguring Codex', () => {
  const manifest = validManifest();
  manifest.agents[1].model = 'ark';
  manifest.agents[1].volcSessionId = 'sesn-beta-self-hosted';
  manifest.agents[1].volcEnvironmentId = 'env-beta-self-hosted';
  withManifest(manifest, (manifestPath) => {
    const env = { VIKA_CONFIG: manifestPath };
    const deployment = loadDeploymentManifest(env);
    applyDeploymentAgentEnv(env, deployment);
    assert.equal(env.VIKA_MODEL, 'ark');
    assert.equal(env.VIKA_CODEX_MODEL, undefined);
    assert.equal(env.VIKA_VOLC_SESSION_ID, 'sesn-beta-self-hosted');
    assert.equal(env.VIKA_VOLC_ENVIRONMENT_ID, 'env-beta-self-hosted');
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
        () => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }),
        error,
        name,
      );
    });
  }
});

test('connects mailboxes only together with the email connector, each account file once', () => {
  const manifest = validManifest();
  const mailboxes = [{ id: 'email-gmail', config: '/srv/vika/secrets/beta-gmail.json' }, { id: 'email-qq', config: '/srv/vika/secrets/beta-qq.json' }];
  manifest.agents[1].connectors = ['email', 'lark'];
  manifest.agents[1].emailAccounts = mailboxes;
  withManifest(manifest, (manifestPath) => {
    const env = { VIKA_CONFIG: manifestPath, VIKA_EMAIL_ACCOUNTS_JSON: '[]' };
    const deployment = loadDeploymentManifest(env);
    // A mailbox is never the primary IM, wherever it is listed.
    assert.equal(selectDeploymentAgent(deployment).channel, 'lark');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.VIKA_CONNECTORS_JSON, '["email","lark"]');
    assert.deepEqual(JSON.parse(env.VIKA_EMAIL_ACCOUNTS_JSON), mailboxes);
    applyDeploymentAgentEnv(env, deployment, 'gamma');
    assert.equal(env.VIKA_EMAIL_ACCOUNTS_JSON, undefined);
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
      assert.throws(() => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }), error);
    });
  }
});


test('OV recall is opt-in per instance, with actor scope and no Lark profile for Weixin', () => {
  const manifest = validManifest();
  manifest.agents[1].ovAutoRecall = true;
  manifest.agents[2].channel = 'weixin';
  manifest.agents[2].connectors = ['weixin'];
  delete manifest.agents[2].profile;
  withManifest(manifest, (manifestPath) => {
    const env = {
      VIKA_CONFIG: manifestPath,
      OPENVIKING_CLI_CONFIG_FILE: '/stale/ovcli.conf',
      VIKA_LARK_PROFILE: 'stale',
      LARKSUITE_CLI_PROFILE: 'stale',
    };
    const deployment = loadDeploymentManifest(env);
    assert.equal(selectDeploymentAgent(deployment, 'alpha').ovAutoRecall, false);
    applyDeploymentAgentEnv(env, deployment, 'alpha');
    assert.equal(env.OPENVIKING_CLI_CONFIG_FILE, '/srv/vika/ov/alpha.json');
    assert.equal(env.OPENVIKING_MEMORY_ENABLED, '1');
    assert.equal(env.OPENVIKING_AUTO_RECALL, '0');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.OPENVIKING_CLI_CONFIG_FILE, '/srv/vika/ov/beta.json');
    assert.equal(env.OPENVIKING_MEMORY_ENABLED, '1');
    assert.equal(env.OPENVIKING_AUTO_RECALL, '1');
    assert.equal(env.OPENVIKING_RECALL_PEER_SCOPE, 'actor');
    assert.equal(env.LARKSUITE_CLI_PROFILE, 'cli_beta');
    applyDeploymentAgentEnv(env, deployment, 'gamma');
    assert.equal(selectDeploymentAgent(deployment, 'gamma').profile, null);
    assert.equal(env.VIKA_LARK_PROFILE, undefined);
    assert.equal(env.LARKSUITE_CLI_PROFILE, undefined);
  });
});

test('explicit silence is opt-in per instance and always pinned in the daemon env', () => {
  const manifest = validManifest();
  manifest.agents[1].explicitSilence = true;
  withManifest(manifest, (manifestPath) => {
    const env = { VIKA_CONFIG: manifestPath, VIKA_EXPLICIT_SILENCE: 'true' };
    const deployment = loadDeploymentManifest(env);
    assert.equal(selectDeploymentAgent(deployment, 'alpha').explicitSilence, false);
    applyDeploymentAgentEnv(env, deployment, 'alpha');
    assert.equal(env.VIKA_EXPLICIT_SILENCE, 'false');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.VIKA_EXPLICIT_SILENCE, 'true');
  });
  const broken = validManifest();
  broken.agents[0].explicitSilence = 'true';
  withManifest(broken, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }), /agents\[0\]\.explicitSilence must be a boolean/);
  });
});

test('staged replies are opt-in per instance and always pinned in the daemon env', () => {
  const manifest = validManifest();
  manifest.agents[1].stagedReplies = true;
  withManifest(manifest, (manifestPath) => {
    const env = { VIKA_CONFIG: manifestPath, VIKA_STAGED_REPLIES: 'true' };
    const deployment = loadDeploymentManifest(env);
    assert.equal(selectDeploymentAgent(deployment, 'alpha').stagedReplies, false);
    applyDeploymentAgentEnv(env, deployment, 'alpha');
    assert.equal(env.VIKA_STAGED_REPLIES, 'false');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.VIKA_STAGED_REPLIES, 'true');
  });
  const broken = validManifest();
  broken.agents[0].stagedReplies = 1;
  withManifest(broken, (manifestPath) => {
    assert.throws(() => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }), /agents\[0\]\.stagedReplies must be a boolean/);
  });
});

test('an instance names a shipped role or its own role file, and keeps natural writing unless it turns it off', () => {
  const manifest = validManifest();
  manifest.agents[0].role = 'assistant';
  manifest.agents[1].role = '/srv/vika/roles/beta.md';
  manifest.agents[1].naturalWriting = false;
  withManifest(manifest, (manifestPath) => {
    const env = { VIKA_CONFIG: manifestPath, VIKA_ROLE: 'qa', VIKA_NATURAL_WRITING: 'false' };
    const deployment = loadDeploymentManifest(env);
    assert.equal(selectDeploymentAgent(deployment, 'alpha').naturalWriting, true);
    applyDeploymentAgentEnv(env, deployment, 'alpha');
    assert.equal(env.VIKA_ROLE, 'assistant');
    assert.equal(env.VIKA_NATURAL_WRITING, 'true');
    applyDeploymentAgentEnv(env, deployment, 'beta');
    assert.equal(env.VIKA_ROLE, '/srv/vika/roles/beta.md');
    assert.equal(env.VIKA_NATURAL_WRITING, 'false');
    // No role: neither the shell's nor another instance's leaks in.
    applyDeploymentAgentEnv(env, deployment, 'gamma');
    assert.equal(env.VIKA_ROLE, undefined);
  });
  for (const [field, value, message] of [
    ['role', 'pirate', /agents\[0\]\.role must be colleague, assistant, qa or an absolute path/],
    ['role', 'roles/mine.md', /agents\[0\]\.role must be colleague, assistant, qa or an absolute path/],
    ['naturalWriting', 'no', /agents\[0\]\.naturalWriting must be a boolean/],
  ]) {
    const broken = validManifest();
    broken.agents[0][field] = value;
    withManifest(broken, (manifestPath) => {
      assert.throws(() => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }), message);
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
        () => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }),
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
      assert.throws(() => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }), /cannot be all, web, updater/, id);
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
      (manifest) => { manifest.agents[1].dataDir = '/srv/vika/data/alpha'; },
      /duplicate agent dataDir: \/srv\/vika\/data\/alpha/,
    ],
    [
      'workspace',
      (manifest) => { manifest.agents[1].workspace = '/srv/vika/workspaces/alpha'; },
      /duplicate agent workspace: \/srv\/vika\/workspaces\/alpha/,
    ],
    [
      'ovConfig',
      (manifest) => { manifest.agents[1].ovConfig = '/srv/vika/ov/alpha.json'; },
      /duplicate agent ovConfig: \/srv\/vika\/ov\/alpha\.json/,
    ],
  ];

  for (const [name, mutate, error] of cases) {
    const manifest = validManifest();
    mutate(manifest);
    withManifest(manifest, (manifestPath) => {
      assert.throws(
        () => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }),
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
      (manifest) => { manifest.agents[1].workspace = '/srv/vika/data/alpha/nested'; },
      /agent storage paths overlap: agents\[0\]\.dataDir and agents\[1\]\.workspace/,
    ],
    [
      'same-agent overlap',
      (manifest) => { manifest.agents[0].workspace = '/srv/vika/data/alpha/work'; },
      /agent storage paths overlap: agents\[0\]\.dataDir and agents\[0\]\.workspace/,
    ],
  ];

  for (const [name, mutate, error] of cases) {
    const manifest = validManifest();
    mutate(manifest);
    withManifest(manifest, (manifestPath) => {
      assert.throws(
        () => loadDeploymentManifest({ VIKA_CONFIG: manifestPath }),
        error,
        name,
      );
    });
  }
});
