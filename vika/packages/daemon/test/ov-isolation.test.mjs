// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import {
  OV_PLUGIN_ID, claudeIsolationSettings, codexConfigFile, codexIsolationArgs, piIsolationArgs, prepareOpencodeConfigHome,
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
  assert.equal(traexConfigFile({ ...env, TRAE_HOME: '/srv/trae' }), '/srv/trae/traecli.toml');
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

test('Pi loads its built-ins and the owner\'s extensions by path, never OpenViking\'s', (t) => {
  const { write, env } = home(t);
  write('.pi/agent/extensions/openviking/index.ts', '');
  write('.pi/agent/extensions/my-provider/index.ts', '');
  write('.pi/agent/extensions/lint.ts', '');
  write('.pi/agent/extensions/notes.txt', '');
  const args = piIsolationArgs(env);
  assert.equal(args[0], '-ne', 'the short flag: Pi ignores the long one next to --extension');
  const loaded = args.filter((_, index) => args[index - 1] === '--extension');
  assert.deepEqual(loaded.slice(0, 2), ['builtin:mcp', 'builtin:llama.cpp']);
  assert.deepEqual(loaded.slice(2).map((file) => path.basename(file)).sort(), ['lint.ts', 'my-provider']);
  assert.deepEqual(piIsolationArgs({ HOME: path.join(env.HOME, 'nobody') }).filter((arg) => arg !== '--extension'),
    ['-ne', 'builtin:mcp', 'builtin:llama.cpp']);
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
  const before = fs.readFileSync(path.join(directory, '.config/opencode/opencode.jsonc'), 'utf8');
  const target = prepareOpencodeConfigHome(path.join(directory, 'vika-home'), env);
  const config = JSON.parse(fs.readFileSync(path.join(target, 'opencode', 'opencode.json'), 'utf8'));
  assert.deepEqual(config.plugin, ['opencode-wakatime']);
  assert.deepEqual(Object.keys(config.mcp), ['github']);
  assert.equal(config.provider.ark.options.baseURL, 'https://ark.example/api/v3', 'a // inside a string survives');
  assert.deepEqual(fs.readdirSync(path.join(target, 'opencode', 'plugins')), ['wakatime.js']);
  assert.equal(fs.readFileSync(path.join(target, 'opencode', 'agents', 'reviewer.md'), 'utf8'), '# reviewer');
  assert.equal(fs.readFileSync(path.join(target, 'git', 'config'), 'utf8'), '[user]');
  assert.equal(fs.readFileSync(path.join(directory, '.config/opencode/opencode.jsonc'), 'utf8'), before, 'the owner\'s file is untouched');
  // Built again on the next start, from scratch.
  prepareOpencodeConfigHome(target, env);
  assert.deepEqual(fs.readdirSync(path.join(target, 'opencode', 'plugins')), ['wakatime.js']);
});
