// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import {
  OV_PLUGIN_ID, PI_BUILTIN_EXTENSIONS, claudeIsolationSettings, codexConfigFile, codexIsolationArgs, piIsolationArgs, prepareOpencodeConfigHome,
  traexConfigFile,
} from '../src/ov-isolation.mjs';
import { OV_ISOLATION, RUNTIME_CATALOG } from '../src/runtime-catalog.mjs';

function home(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'vika-ov-isolation-'));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const write = (relative, text) => {
    const file = path.join(directory, relative);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, typeof text === 'string' ? text : JSON.stringify(text));
    return file;
  };
  return { directory, write, env: { HOME: directory } };
}

test('every runtime declares how it keeps its own OpenViking plugin out', () => {
  for (const runtime of Object.values(RUNTIME_CATALOG)) {
    assert.ok(OV_ISOLATION.includes(runtime.ovIsolation), `${runtime.id} declares ovIsolation`);
  }
  // A runtime that runs on this machine cannot claim to load nothing from it.
  for (const runtime of Object.values(RUNTIME_CATALOG).filter((item) => item.capabilities.agentTools === 'core')) {
    assert.notEqual(runtime.ovIsolation, 'remote', runtime.id);
  }
});

test('Codex and TraeX switch off every OpenViking plugin and MCP server in their config, and their own memory', (t) => {
  const { write, env } = home(t);
  const config = write('.codex/config.toml', [
    'model = "x"',
    '[plugins."openviking-memory@community"]', 'enabled = true',
    '[plugins."sites@openai-curated"]', 'enabled = true',
    '[mcp_servers.openviking]', 'command = "node"',
    '[mcp_servers.memory]', 'command = "node"', 'args = ["/opt/openviking/servers/mcp-proxy.mjs"]',
    '[mcp_servers.botmux]', 'command = "botmux"',
  ].join('\n'));
  assert.equal(codexConfigFile(env), config);
  const args = codexIsolationArgs(config);
  const overrides = args.filter((_, index) => args[index - 1] === '-c');
  assert.deepEqual(overrides, [
    `plugins.${OV_PLUGIN_ID}.enabled=false`,
    'plugins.openviking-memory@community.enabled=false',
    'mcp_servers.openviking.enabled=false',
    'mcp_servers.memory.enabled=false',
    'features.memories=false',
  ]);
  assert.equal(args.length, overrides.length * 2, 'every value follows its -c');
  // No config file: the official plugin id and the memory feature still go.
  assert.deepEqual(codexIsolationArgs(path.join(env.HOME, 'missing.toml')),
    ['-c', `plugins.${OV_PLUGIN_ID}.enabled=false`, '-c', 'features.memories=false']);
  assert.equal(traexConfigFile(env), path.join(env.HOME, '.trae', 'traecli.toml'));
  const traeHome = path.join(env.HOME, 'trae home');
  assert.equal(traexConfigFile({ ...env, TRAE_HOME: traeHome }), path.join(traeHome, 'traecli.toml'));
});

test('Claude Code gets one settings layer that disables OpenViking\'s plugins and denies its servers, and nothing else', (t) => {
  const { write, env, directory } = home(t);
  write('.claude/settings.json', { enabledPlugins: { 'openviking-memory@openviking': true, 'cc-plugin-agents-md@builtin': true } });
  const workspace = path.join(directory, 'ws');
  write('.claude.json', {
    mcpServers: { ov: { command: 'node', args: ['/x/openviking/mcp-proxy.mjs'] }, github: { command: 'gh-mcp' } },
    projects: { [workspace]: { mcpServers: { 'openviking-project': { type: 'http', url: 'http://h/mcp' } } } },
  });
  write('ws/.mcp.json', { mcpServers: { personal_agent: { command: 'node' }, docs: { command: 'docs' } } });
  assert.deepEqual(claudeIsolationSettings({ env, workspace }), {
    enabledPlugins: { [OV_PLUGIN_ID]: false },
    deniedMcpServers: [{ serverName: 'openviking' }, { serverName: 'ov' }, { serverName: 'openviking-project' }],
  });
});

test('Pi loads its built-ins and the owner\'s extensions by path, never OpenViking\'s', async (t) => {
  const { write, env } = home(t);
  write('.pi/agent/extensions/openviking/index.ts', '');
  write('.pi/agent/extensions/my-provider/index.ts', '');
  write('.pi/agent/extensions/lint.ts', '');
  write('.pi/agent/extensions/notes.txt', '');
  const args = await piIsolationArgs(env, env.HOME);
  assert.equal(args[0], '-ne', 'the short flag: Pi ignores the long one next to --extension');
  const loaded = args.filter((_, index) => args[index - 1] === '--extension');
  assert.deepEqual(loaded.filter((entry) => entry.startsWith('builtin:')), PI_BUILTIN_EXTENSIONS);
  assert.deepEqual(loaded.filter((entry) => !entry.startsWith('builtin:')).map((file) => path.basename(file)).sort(), ['index.ts', 'lint.ts']);
  assert.deepEqual((await piIsolationArgs({ HOME: path.join(env.HOME, 'nobody') }, env.HOME)).filter((arg) => arg !== '--extension'),
    ['-ne', ...PI_BUILTIN_EXTENSIONS]);
});

test('OpenCode reads a Vika-owned config home: the owner\'s config without OpenViking, every other tool\'s config linked', (t) => {
  const { write, env, directory } = home(t);
  write('.config/opencode/opencode.jsonc', `{
    // the owner's providers
    "provider": { "ark": { "options": { "baseURL": "https://ark.example/api/v3" } } },
    "plugin": ["@openviking/opencode-plugin", "opencode-wakatime",],
    "mcp": { "openviking": { "type": "local", "command": ["node", "mcp-proxy.mjs"] }, "github": { "type": "remote", "url": "https://gh/mcp" } }
  }`);
  write('.config/opencode/plugins/openviking.js', 'export {}');
  write('.config/opencode/plugins/wakatime.js', 'export {}');
  write('.config/opencode/agents/reviewer.md', '# reviewer');
  write('.config/git/config', '[user]');
  write('.config/tool.conf', 'keep top-level file');
  const before = fs.readFileSync(path.join(directory, '.config/opencode/opencode.jsonc'), 'utf8');
  const target = prepareOpencodeConfigHome(path.join(directory, 'vika-home'), env);
  const config = JSON.parse(fs.readFileSync(path.join(target, 'opencode', 'opencode.json'), 'utf8'));
  assert.deepEqual(config.plugin, ['opencode-wakatime']);
  assert.deepEqual(Object.keys(config.mcp), ['github']);
  assert.equal(fs.readFileSync(path.join(target, 'tool.conf'), 'utf8'), 'keep top-level file');
  assert.equal(config.provider.ark.options.baseURL, 'https://ark.example/api/v3', 'a // inside a string survives');
  assert.deepEqual(fs.readdirSync(path.join(target, 'opencode', 'plugins')), ['wakatime.js']);
  assert.equal(fs.readFileSync(path.join(target, 'opencode', 'agents', 'reviewer.md'), 'utf8'), '# reviewer');
  assert.equal(fs.readFileSync(path.join(target, 'git', 'config'), 'utf8'), '[user]');
  assert.equal(fs.readFileSync(path.join(directory, '.config/opencode/opencode.jsonc'), 'utf8'), before, 'the owner\'s file is untouched');
  // Rebuilding must refresh file snapshots and never delete junction targets.
  write('.config/opencode/plugins/wakatime.js', 'export const updated = true');
  prepareOpencodeConfigHome(target, env);
  assert.equal(fs.readFileSync(path.join(target, 'opencode', 'plugins', 'wakatime.js'), 'utf8'), 'export const updated = true');
  assert.equal(fs.readFileSync(path.join(directory, '.config/git/config'), 'utf8'), '[user]');
  assert.equal(fs.readFileSync(path.join(directory, '.config/opencode/agents/reviewer.md'), 'utf8'), '# reviewer');
  assert.deepEqual(fs.readdirSync(path.join(target, 'opencode', 'plugins')), ['wakatime.js']);
});


test('Pi resolves configured and package extensions in both scopes, retaining filters and all original settings', async (t) => {
  const { write, env, directory } = home(t);
  const body = 'export default function() {}';
  const configured = write('.pi/agent/custom/configured.mjs', body);
  const disabled = write('.pi/agent/extensions/disabled.js', body);
  const project = write('ws/.pi/custom/configured.mjs', body);
  const automatic = write('ws/.pi/extensions/project.js', body);
  write('ws/.pi/extensions/openviking/index.js', body);
  write('.pi/agent/custom/openviking.js', body);
  // A package's manifest, not just its entrypoint basename, identifies OV.
  write('.pi/agent/packages/neutral/package.json', { name: '@openviking/pi-extension', pi: { extensions: ['./index.js'] } });
  write('.pi/agent/packages/neutral/index.js', body);
  write('.pi/agent/packages/keep/package.json', { name: '@fixture/tools', pi: { extensions: ['./index.js', './unused.js'] } });
  const packaged = write('.pi/agent/packages/keep/index.js', body);
  const unused = write('.pi/agent/packages/keep/unused.js', body);
  write('ws/.pi/packages/keep/package.json', { name: '@fixture/project-tools', pi: { extensions: ['./index.js'] } });
  const projectPackage = write('ws/.pi/packages/keep/index.js', body);
  const globalSettings = write('.pi/agent/settings.json', {
    extensions: ['./custom/configured.mjs', './custom/openviking.js', '-extensions/disabled.js', '-builtin:llama.cpp'],
    packages: [{ source: './packages/keep', extensions: ['index.js'] }, './packages/neutral'],
  });
  const projectSettings = write('ws/.pi/settings.json', { extensions: ['./custom/configured.mjs'], packages: ['./packages/keep'] });
  const before = [globalSettings, projectSettings].map((file) => fs.readFileSync(file, 'utf8'));
  const args = await piIsolationArgs(env, path.join(directory, 'ws'));
  const loaded = args.filter((_, i) => args[i - 1] === '--extension');
  for (const file of [configured, project, automatic, packaged, projectPackage, 'builtin:mcp', 'builtin:codemode', 'builtin:tool-search']) {
    assert.ok(loaded.includes(file), `keeps ${path.basename(file)}`);
  }
  for (const file of [disabled, unused, 'builtin:llama.cpp']) assert.ok(!loaded.includes(file), `respects disabling ${path.basename(file)}`);
  assert.equal(loaded.some((file) => /openviking|neutral/.test(file)), false);
  assert.deepEqual([globalSettings, projectSettings].map((file) => fs.readFileSync(file, 'utf8')), before);
});

test('Windows config projection reports copy failures instead of dropping plugins', { skip: process.platform !== 'win32' }, (t) => {
  const { write, env, directory } = home(t);
  write('.config/opencode/plugins/keep.js', 'export {}');
  t.mock.method(fs, 'copyFileSync', () => { throw Object.assign(new Error('fixture access denied'), { code: 'EACCES' }); });
  assert.throws(() => prepareOpencodeConfigHome(path.join(directory, 'vika-home'), env), { code: 'EACCES' });
});
