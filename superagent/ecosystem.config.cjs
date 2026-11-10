// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

'use strict';

const { normalizeEnv } = require('./env-compat.cjs');
const fs = require('node:fs');
const { homeDir: platformHomeDir } = require('./packages/daemon/src/platform/index.mjs');
const { capabilities } = require('./packages/daemon/src/capabilities.mjs');
const { ecosystemSearchPath, pm2Options } = require('./packages/updater/src/pm2-platform.mjs');
const path = require('node:path');
const {
  CONTROL_ENV_MAX_BYTES,
  deploymentDaemonEnvOverrides,
  loadDeploymentManifest,
  openVikingAgentEnv,
  resolvedDeploymentDaemonEnv,
} = require('./deployment-config.cjs');

const releaseDir = __dirname;
const daemonDir = path.join(releaseDir, 'packages', 'daemon');
const webDir = path.join(releaseDir, 'packages', 'web');
const updaterDir = path.join(releaseDir, 'packages', 'updater');
// The caller's PATH first; the usual system directories only as a fallback.
// Put first, they once handed the managed Pi a system Node v10.
const fallbackPath = '/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin';
// An installed release runs on the Node it ships; a source checkout on the
// Node running pm2. Windows packages keep it in runtime\, POSIX ones in node/bin/.
const releaseNode = [
  path.join(releaseDir, 'node', 'bin', 'node'),
  path.join(releaseDir, 'runtime', 'node.exe'),
].find(candidate => fs.existsSync(candidate));

const common = Object.freeze({
  interpreter: releaseNode ?? process.execPath,
  exec_mode: 'fork',
  ...pm2Options(),
  instances: 1,
  autorestart: true,
  wait_ready: true,
  time: false,
  merge_logs: true,
  filter_env: ['OPENVIKING_'],
});

function defined(values) {
  return Object.fromEntries(Object.entries(values).filter(([, value]) => value !== undefined));
}

function buildEcosystem(sourceEnv = process.env) {
  sourceEnv = normalizeEnv(sourceEnv);
  const homeDir = platformHomeDir(sourceEnv);
  if (!homeDir || !path.isAbsolute(homeDir)) throw new Error('HOME must be an absolute path');
  const baseDir = path.join(homeDir, '.superagent');
  const deployment = loadDeploymentManifest(sourceEnv);
  if (deployment.agents.some((agent) => agent.port === 3737)) {
    throw new Error('agent port 3737 conflicts with the shared Web port');
  }
  const sharedDaemonEnv = resolvedDeploymentDaemonEnv(deployment, sourceEnv);
  const systemDaemonEnvOverrides = deploymentDaemonEnvOverrides(sourceEnv);

  const daemonApps = deployment.agents.map((agent) => ({
    ...common,
    name: `superagent-${agent.id}`,
    script: path.join(daemonDir, 'src', 'main.mjs'),
    cwd: daemonDir,
    restart_delay: 2000,
    min_uptime: '30s',
    max_restarts: 10,
    kill_timeout: 30000,
    listen_timeout: 30000,
    env: defined({
      ...sharedDaemonEnv,
      NODE_ENV: 'production',
      OV_SUPERAGENT_CONFIG: deployment.manifestPath,
      OV_SUPERAGENT_INSTANCE_ID: agent.id,
      OV_SUPERAGENT_UPGRADE_TARGET: agent.id,
      OV_SUPERAGENT_HOST: '127.0.0.1',
      OV_SUPERAGENT_HTTP_ENABLED: 'true',
      OV_SUPERAGENT_CHANNEL: agent.channel,
      OV_SUPERAGENT_CONNECTORS_JSON: JSON.stringify(agent.connectors),
      OV_SUPERAGENT_EMAIL_ACCOUNTS_JSON: agent.emailAccounts.length > 0 ? JSON.stringify(agent.emailAccounts) : undefined,
      OV_SUPERAGENT_PORT: String(agent.port),
      // Empty for an instance without the lark connector, so a profile in
      // pm2's own environment cannot leak into it.
      OV_SUPERAGENT_LARK_PROFILE: agent.profile || '',
      LARKSUITE_CLI_PROFILE: agent.profile || '',
      OV_SUPERAGENT_AGENT_NAME: agent.displayName,
      OV_SUPERAGENT_DATA_DIR: agent.dataDir,
      OV_SUPERAGENT_TEMP_DIR: agent.tempDir || sharedDaemonEnv.OV_SUPERAGENT_TEMP_DIR || '',
      OV_SUPERAGENT_WORKSPACE: agent.workspace,
      OV_SUPERAGENT_MODEL: agent.model,
      // Empty values deliberately override any legacy global seed. Managed
      // Agent identity must remain pinned to one daemon/profile instance.
      OV_SUPERAGENT_VOLC_SESSION_ID: agent.volcSessionId || '',
      OV_SUPERAGENT_VOLC_ENVIRONMENT_ID: agent.volcEnvironmentId || '',
      ...openVikingAgentEnv(agent),
      PATH: ecosystemSearchPath(sourceEnv, fallbackPath),
    }),
  }));

  const webTargets = deployment.agents.map((agent) => ({
    id: agent.id,
    label: agent.displayName,
    host: '127.0.0.1',
    port: agent.port,
  }));
  const webTargetsJson = JSON.stringify(webTargets);
  for (const [name, value] of [
    ['OV_SUPERAGENT_WEB_INSTANCES', webTargetsJson],
  ]) {
    if (Buffer.byteLength(value) > CONTROL_ENV_MAX_BYTES) {
      throw new Error(`${name} exceeds ${CONTROL_ENV_MAX_BYTES} bytes`);
    }
  }

  return {
    apps: [
      ...daemonApps,
      {
        ...common,
        filter_env: [
          ...common.filter_env,
          'ARK_',
          'VOLCENGINE_',
          'OV_SUPERAGENT_VOLC_',
          'SESSION_ID',
        ],
        name: 'superagent-web',
        script: path.join(webDir, 'src', 'main.mjs'),
        cwd: webDir,
        restart_delay: 1000,
        min_uptime: '10s',
        max_restarts: 10,
        kill_timeout: 5000,
        listen_timeout: 10000,
        env: {
          NODE_ENV: 'production',
          OV_SUPERAGENT_WEB_HOST: '127.0.0.1',
          OV_SUPERAGENT_WEB_PORT: '3737',
          OV_SUPERAGENT_WEB_DEFAULT_INSTANCE: deployment.defaultInstance,
          OV_SUPERAGENT_WEB_INSTANCES: webTargetsJson,
          PATH: ecosystemSearchPath(sourceEnv, fallbackPath),
        },
      },
      ...(capabilities.updater ? [{
        ...common,
        name: 'superagent-updater',
        script: path.join(updaterDir, 'src', 'main.mjs'),
        cwd: updaterDir,
        restart_delay: 1000,
        min_uptime: '10s',
        max_restarts: 10,
        kill_timeout: 5000,
        listen_timeout: 10000,
        env: {
          ...systemDaemonEnvOverrides,
          NODE_ENV: 'production',
          OV_SUPERAGENT_CONFIG: deployment.manifestPath,
          OV_SUPERAGENT_UPDATER_RELEASE_LINK: path.join(baseDir, 'current'),
          OV_SUPERAGENT_UPDATER_SOCKET: path.join(baseDir, 'updater.sock'),
          // The updater drives the same pm2 daemon the vika command uses.
          PM2_HOME: sourceEnv.PM2_HOME || path.join(baseDir, 'pm2'),
          PM2_BIN: path.join(releaseDir, 'bin', 'pm2'),
          PATH: ecosystemSearchPath(sourceEnv, fallbackPath),
        },
      }] : []),
    ],
  };
}

module.exports = {
  ...buildEcosystem(),
  buildEcosystem,
};
