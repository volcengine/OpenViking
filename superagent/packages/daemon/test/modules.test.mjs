// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { describe, it } from 'node:test';
import { projectDir as daemonProjectDir } from '../src/config.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

function sourceFiles(directory) {
  return fs.readdirSync(directory)
    .filter((name) => /\.(?:mjs|js)$/.test(name))
    .map((name) => path.join(directory, name));
}

describe('module boundaries', () => {
  it('loads daemon configuration from the release root', () => {
    assert.equal(daemonProjectDir, root);
  });

  it('builds every daemon plus shared web and updater from one manifest', () => {
    const require = createRequire(import.meta.url);
    const ecosystemPath = path.join(root, 'ecosystem.config.cjs');
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'lark-ecosystem-'));
    const manifestPath = path.join(directory, 'config.json');
    fs.writeFileSync(manifestPath, `${JSON.stringify({
      version: 1,
      defaultInstance: 'beta',
      env: {
        OV_SUPERAGENT_LOG_LEVEL: 'info',
        OV_SUPERAGENT_DRY_RUN: 'false',
      },
      agents: [
        {
          id: 'alpha',
          displayName: 'Alpha',
          profile: 'cli_alpha',
          port: 3738,
          dataDir: '/Users/test/.ov-superagent/data/alpha',
          tempDir: '/tmp/superagent-alpha',
          workspace: '/Users/test/Documents/work/alpha-workspace',
          ovConfig: '/Users/test/.openviking/ovcli.conf.alpha',
          model: 'gpt-5.6-sol',
        },
        {
          id: 'beta',
          displayName: 'Beta',
          profile: 'cli_beta',
          port: 3740,
          dataDir: '/Users/test/.ov-superagent/data/beta',
          workspace: '/Users/test/Documents/work/beta-workspace',
          ovConfig: '/Users/test/.openviking/ovcli.conf.beta',
          ovAutoRecall: true,
          model: 'ark',
          volcSessionId: 'sesn-beta-self-hosted',
          volcEnvironmentId: 'env-beta-self-hosted',
        },
      ],
    }, null, 2)}\n`);
    const env = {
      HOME: '/Users/test',
      PATH: '/safe/bin',
      OV_SUPERAGENT_CONFIG: manifestPath,
      OV_SUPERAGENT_LOG_LEVEL: 'debug',
      OV_SUPERAGENT_TEMP_DIR: '/tmp/superagent-default',
      ARK_API_KEY: 'system-only-secret',
      OV_SUPERAGENT_VOLC_SESSION_ID: 'seed-session-ref',
      SESSION_ID: 'generic-session-ref',
    };
    const original = Object.fromEntries(Object.keys(env).map((key) => [key, process.env[key]]));
    Object.assign(process.env, env);
    let ecosystem;
    let manifestOnlyEcosystem;
    try {
      delete require.cache[require.resolve(ecosystemPath)];
      const { buildEcosystem } = require(ecosystemPath);
      ecosystem = buildEcosystem(env);
      manifestOnlyEcosystem = buildEcosystem({
        HOME: '/Users/test',
        PATH: '/safe/bin',
        OV_SUPERAGENT_CONFIG: manifestPath,
      });
    } finally {
      for (const [key, value] of Object.entries(original)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
      fs.rmSync(directory, { recursive: true, force: true });
    }
    assert.deepEqual(
      ecosystem.apps.map((app) => app.name),
      ['superagent-alpha', 'superagent-beta', 'superagent-web', 'superagent-updater'],
    );
    const [alpha, beta, web, updater] = ecosystem.apps;
    assert.match(alpha.script, /packages\/daemon\/src\/main\.mjs$/);
    assert.match(web.script, /packages\/web\/src\/main\.mjs$/);
    assert.equal(alpha.env.OV_SUPERAGENT_PORT, '3738');
    assert.equal(alpha.env.OV_SUPERAGENT_TEMP_DIR, '/tmp/superagent-alpha');
    assert.equal(beta.env.OV_SUPERAGENT_TEMP_DIR, '/tmp/superagent-default');
    assert.equal(manifestOnlyEcosystem.apps[0].env.OV_SUPERAGENT_TEMP_DIR, '/tmp/superagent-alpha');
    assert.equal(manifestOnlyEcosystem.apps[1].env.OV_SUPERAGENT_TEMP_DIR, '');
    assert.equal(alpha.env.OV_SUPERAGENT_HTTP_ENABLED, 'true');
    assert.equal(alpha.env.OV_SUPERAGENT_LARK_PROFILE, 'cli_alpha');
    assert.equal(alpha.env.LARKSUITE_CLI_PROFILE, 'cli_alpha');
    assert.equal(alpha.env.OV_SUPERAGENT_AGENT_NAME, 'Alpha');
    assert.equal(alpha.env.OV_SUPERAGENT_UPGRADE_TARGET, 'alpha');
    assert.equal(alpha.env.OPENVIKING_CLI_CONFIG_FILE, '/Users/test/.openviking/ovcli.conf.alpha');
    // Recall is the daemon's; the plugins' own is off on every instance.
    assert.equal(alpha.env.OPENVIKING_AUTO_RECALL, '0');
    assert.equal(beta.env.OPENVIKING_AUTO_RECALL, '0');
    assert.equal(beta.env.OPENVIKING_RECALL_PEER_SCOPE, 'all');
    assert.equal(alpha.env.OPENVIKING_AUTO_CAPTURE, '0');
    assert.equal(alpha.env.OPENVIKING_PENDING_DIR, '/Users/test/.ov-superagent/data/alpha/openviking/pending');
    assert.equal(alpha.env.OV_SUPERAGENT_CODEX_MODEL, undefined);
    assert.equal(alpha.env.OV_SUPERAGENT_MODEL, 'gpt-5.6-sol');
    assert.equal(alpha.env.OV_SUPERAGENT_LOG_LEVEL, 'debug');
    assert.equal(alpha.env.OV_SUPERAGENT_DRY_RUN, 'false');
    assert.equal(alpha.env.ARK_API_KEY, 'system-only-secret');
    assert.equal(alpha.env.OV_SUPERAGENT_VOLC_SESSION_ID, '');
    assert.equal(alpha.env.OV_SUPERAGENT_VOLC_ENVIRONMENT_ID, '');
    assert.equal(beta.env.OV_SUPERAGENT_LARK_PROFILE, 'cli_beta');
    assert.equal(beta.env.OV_SUPERAGENT_MODEL, 'ark');
    assert.equal(beta.env.OV_SUPERAGENT_CODEX_MODEL, undefined);
    assert.equal(beta.env.OV_SUPERAGENT_VOLC_SESSION_ID, 'sesn-beta-self-hosted');
    assert.equal(beta.env.OV_SUPERAGENT_VOLC_ENVIRONMENT_ID, 'env-beta-self-hosted');
    assert.equal(web.env.OV_SUPERAGENT_WEB_HOST, '127.0.0.1');
    assert.equal(web.env.OV_SUPERAGENT_WEB_PORT, '3737');
    assert.equal(web.env.OV_SUPERAGENT_WEB_DEFAULT_INSTANCE, 'beta');
    const effectiveWebEnv = Object.fromEntries(Object.entries({
      ...env,
      ...web.env,
    }).filter(([key]) => !web.filter_env.some((prefix) => key.startsWith(prefix))));
    assert.equal(effectiveWebEnv.ARK_API_KEY, undefined);
    assert.equal(effectiveWebEnv.OV_SUPERAGENT_VOLC_SESSION_ID, undefined);
    assert.equal(effectiveWebEnv.SESSION_ID, undefined);
    assert.deepEqual(
      JSON.parse(web.env.OV_SUPERAGENT_WEB_INSTANCES),
      [
        { id: 'alpha', label: 'Alpha', host: '127.0.0.1', port: 3738 },
        { id: 'beta', label: 'Beta', host: '127.0.0.1', port: 3740 },
      ],
    );
    assert.match(updater.script, /packages\/updater\/src\/main\.mjs$/);
    assert.match(updater.env.OV_SUPERAGENT_UPDATER_SOCKET, /updater\.sock$/);
    assert.equal(updater.env.OV_SUPERAGENT_CONFIG, manifestPath);
    assert.equal(updater.env.OV_SUPERAGENT_LOG_LEVEL, 'debug');
    assert.equal(updater.env.ARK_API_KEY, 'system-only-secret');
    assert.equal(updater.env.OV_SUPERAGENT_DRY_RUN, undefined);
    assert.equal(web.env.OV_SUPERAGENT_LOG_LEVEL, undefined);
    const [manifestOnlyAlpha, , , manifestOnlyUpdater] = manifestOnlyEcosystem.apps;
    assert.equal(manifestOnlyAlpha.env.OV_SUPERAGENT_LOG_LEVEL, 'info');
    assert.equal(manifestOnlyAlpha.env.OV_SUPERAGENT_DRY_RUN, 'false');
    assert.equal(manifestOnlyUpdater.env.OV_SUPERAGENT_LOG_LEVEL, undefined);
    assert.equal(manifestOnlyUpdater.env.OV_SUPERAGENT_DRY_RUN, undefined);
    // The updater reads its targets from the manifest per request, not a list
    // frozen into its environment at start.
    assert.equal(updater.env.OV_SUPERAGENT_UPDATER_TARGETS_JSON, undefined);
    assert.equal(alpha.wait_ready, true);
    assert.equal(beta.wait_ready, true);
    assert.equal(web.wait_ready, true);
    assert.equal(updater.wait_ready, true);
  });

  it('keeps the web module independent from SQLite and daemon source code', () => {
    const webFiles = sourceFiles(path.join(root, 'packages', 'web', 'src'));
    assert.ok(webFiles.length >= 3);
    const webSource = webFiles.map((file) => fs.readFileSync(file, 'utf8')).join('\n');
    assert.doesNotMatch(webSource, /node:sqlite|store\.mjs|packages\/daemon/);
    assert.doesNotMatch(webSource, /loadProjectEnv|readFileSync\([^)]*\.env/);

    const daemonSource = sourceFiles(path.join(root, 'packages', 'daemon', 'src'))
      .map((file) => fs.readFileSync(file, 'utf8')).join('\n');
    assert.doesNotMatch(daemonSource, /packages\/web|web\/public|activity\.(?:css|js)/);

    const updaterSource = sourceFiles(path.join(root, 'packages', 'updater', 'src'))
      .map((file) => fs.readFileSync(file, 'utf8')).join('\n');
    assert.doesNotMatch(updaterSource, /packages\/daemon|store\.mjs|lark\.mjs/);
  });

  it('runs all package test suites from the root command', () => {
    const manifest = JSON.parse(fs.readFileSync(path.join(root, 'package.json'), 'utf8'));
    assert.match(manifest.scripts.test, /packages\/daemon\/test\/\*\.test\.mjs/);
    assert.match(manifest.scripts.test, /packages\/web\/test\/\*\.test\.mjs/);
    assert.match(manifest.scripts.test, /packages\/updater\/test\/\*\.test\.mjs/);
    assert.match(manifest.scripts.test, /packages\/cli\/test\/\*\.test\.mjs/);
  });

  it('allows exact sibling operations without permitting self or broad PM2 control', () => {
    const prompt = fs.readFileSync(
      path.join(root, 'packages', 'daemon', 'agent', 'core.md'),
      'utf8',
    );
    assert.match(prompt, /Never restart, stop, reconfigure, or redeploy the PM2 application you run in/);
    assert.match(prompt, /The only self-update is `ov-superagent upgrade \{\{OV_SUPERAGENT_UPGRADE_TARGET\}\}`/);
    assert.match(prompt, /installs the latest published release/);
    assert.match(prompt, /Manage another named instance only when directly asked, after verifying that it is not the one you run in/);
    assert.match(prompt, /never use broad PM2 selectors or touch unrelated applications/);
  });
});
