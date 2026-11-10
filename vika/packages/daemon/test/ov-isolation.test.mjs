// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import {
  OV_PLUGIN_ID, PI_BUILTIN_EXTENSIONS, agyWorkspaceOpenVikingPieces, claudeIsolationSettings, codexConfigFile, codexIsolationArgs, piIsolationArgs,
  prepareAgyGeminiDir, prepareOpencodeConfigHome, traexConfigFile,
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


function agyHome(t) {
  const fixture = home(t);
  const { write } = fixture;
  write('.gemini/antigravity-cli/antigravity-oauth-token', 'token');
  write('.gemini/GEMINI.md', '# global');
  write('.gemini/config/AGENTS.md', '# agents');
  write('.gemini/config/.migrated', '');
  write('.gemini/config/projects/one', '');
  write('.gemini/config/openviking-note.md', 'stray');
  write('.gemini/config/mcp_config.json', { mcpServers: {
    openviking: { command: 'ov' },
    wrapped: { command: 'node', args: ['/opt/openviking-mcp.js'] },
    personal: { command: 'tool' },
  }, other: 1 });
  write('.gemini/config/hooks.json', { openviking: { Stop: [] }, wrapper: { Stop: [{ command: 'ov --openviking' }] }, control: { Stop: [] } });
  write('.gemini/config/config.json', { plugins: { 'openviking-memory': { enabled: true }, control: { enabled: false } }, theme: 'dark' });
  write('.gemini/config/plugins/openviking-memory/plugin.json', { name: 'openviking-memory' });
  write('.gemini/config/plugins/neutral-manifest/plugin.json', { name: 'OpenViking Memory' });
  write('.gemini/config/plugins/neutral-hook/plugin.json', { name: 'neutral-hook' });
  write('.gemini/config/plugins/neutral-hook/hooks.json', { openviking: { Stop: [] } });
  write('.gemini/config/plugins/neutral-mcp/mcp_config.json', { mcpServers: { x: { command: '/usr/bin/openviking' } } });
  write('.gemini/config/plugins/control/plugin.json', { name: 'control' });
  write('.gemini/config/skills/openviking-skill/SKILL.md', '# ov');
  write('.gemini/config/skills/control-skill/SKILL.md', '# control');
  write('.gemini/config/rules/openviking.md', '# ov');
  write('.gemini/config/rules/control.md', '# control');
  return fixture;
}

test('Antigravity gets a Vika-owned gemini dir: the owner\'s login and config without OpenViking', (t) => {
  const { directory, env } = agyHome(t);
  const owner = path.join(directory, '.gemini');
  const before = fs.readFileSync(path.join(owner, 'config/hooks.json'), 'utf8');
  const target = prepareAgyGeminiDir(path.join(directory, 'vika-home'), env);
  const config = path.join(target, 'config');
  // Linked whole, so the OAuth token and sessions stay where agy keeps them.
  assert.equal(fs.lstatSync(path.join(target, 'antigravity-cli')).isSymbolicLink(), true);
  assert.equal(fs.readFileSync(path.join(target, 'antigravity-cli', 'antigravity-oauth-token'), 'utf8'), 'token');
  assert.equal(fs.readFileSync(path.join(target, 'GEMINI.md'), 'utf8'), '# global');
  // agy rewrites config.json, so it must be a copy and never a link.
  for (const name of ['mcp_config.json', 'hooks.json', 'config.json']) {
    assert.equal(fs.lstatSync(path.join(config, name)).isSymbolicLink(), false, name);
  }
  const mcp = JSON.parse(fs.readFileSync(path.join(config, 'mcp_config.json'), 'utf8'));
  assert.deepEqual(Object.keys(mcp.mcpServers), ['personal']);
  assert.equal(mcp.other, 1);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(config, 'hooks.json'), 'utf8')), { control: { Stop: [] } });
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(config, 'config.json'), 'utf8')), { plugins: { control: { enabled: false } }, theme: 'dark' });
  assert.deepEqual(fs.readdirSync(path.join(config, 'plugins')), ['control']);
  assert.deepEqual(fs.readdirSync(path.join(config, 'skills')), ['control-skill']);
  assert.deepEqual(fs.readdirSync(path.join(config, 'rules')), ['control.md']);
  assert.equal(fs.readFileSync(path.join(config, 'AGENTS.md'), 'utf8'), '# agents');
  assert.equal(fs.readFileSync(path.join(config, '.migrated'), 'utf8'), '', 'agy must not migrate over the filtered files');
  assert.equal(fs.existsSync(path.join(config, 'projects')), false);
  assert.equal(fs.existsSync(path.join(config, 'openviking-note.md')), false);
  assert.equal(fs.readFileSync(path.join(owner, 'config/hooks.json'), 'utf8'), before, 'the owner\'s file is untouched');
  // Rebuilt each launch; the link removal must not reach through to the owner.
  prepareAgyGeminiDir(path.join(directory, 'vika-home'), env);
  assert.equal(fs.readFileSync(path.join(owner, 'antigravity-cli', 'antigravity-oauth-token'), 'utf8'), 'token');
});

test('Antigravity\'s legacy antigravity-cli files are left to agy: the migration marker stops it overwriting the filtered copies', (t) => {
  const { directory, write, env } = agyHome(t);
  const legacyMcp = write('.gemini/antigravity-cli/mcp_config.json', { mcpServers: { openviking: { command: 'ov' } } });
  const legacyHooks = write('.gemini/antigravity-cli/hooks.json', { openviking: { Stop: [] } });
  const before = [legacyMcp, legacyHooks].map((file) => fs.readFileSync(file, 'utf8'));
  const target = prepareAgyGeminiDir(path.join(directory, 'vika-home'), env);
  assert.equal(fs.statSync(path.join(target, 'config', '.migrated')).size, 0);
  assert.deepEqual(Object.keys(JSON.parse(fs.readFileSync(path.join(target, 'config', 'mcp_config.json'), 'utf8')).mcpServers), ['personal']);
  assert.deepEqual([legacyMcp, legacyHooks].map((file) => fs.readFileSync(file, 'utf8')), before);
  assert.equal(fs.lstatSync(legacyMcp).isSymbolicLink(), false);
});

test('Antigravity with no ~/.gemini yet still gets a usable, empty gemini dir', (t) => {
  const { directory, env } = home(t);
  const target = prepareAgyGeminiDir(path.join(directory, 'vika-home'), env);
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(target, 'config', 'mcp_config.json'), 'utf8')), {});
  assert.equal(fs.existsSync(path.join(target, 'config', 'plugins.json')), false);
});

test('Antigravity declarative configs lose OpenViking\'s entries and keep everything else', (t) => {
  const { directory, write, env } = agyHome(t);
  const external = path.join(directory, 'shared');
  write('shared/ext/openviking-plugin/plugin.json', { name: 'x' });
  write('shared/ext/neutral-ov/hooks.json', { openviking: {} });
  write('shared/ext/control-plugin/plugin.json', { name: 'control-plugin' });
  write('shared/openviking-plugins/x/plugin.json', { name: 'x' });
  write('shared/inherit-ov.json', { entries: [{ path: '/opt/openviking/skills' }] });
  write('shared/inherit-keep.json', { entries: [{ path: '/opt/skills' }] });
  write('.gemini/config/plugins.json', {
    entries: [
      { path: path.join(external, 'ext'), exclude: ['legacy-.*'] },
      { path: path.join(external, 'openviking-plugins') },
      { path: '../relative' },
      { path: '~/personal' },
    ],
    inherits: [{ path: path.join(external, 'inherit-ov.json') }, { path: path.join(external, 'inherit-keep.json') }],
  });
  write('shared/skills/openviking-skill/SKILL.md', '#');
  write('shared/skills/control-skill/SKILL.md', '#');
  write('.gemini/config/skills.json', { entries: [{ path: path.join(external, 'skills') }] });
  const target = prepareAgyGeminiDir(path.join(directory, 'vika-home'), env);
  const plugins = JSON.parse(fs.readFileSync(path.join(target, 'config', 'plugins.json'), 'utf8'));
  assert.deepEqual(plugins.entries, [
    { path: path.join(external, 'ext'), exclude: ['legacy-.*', 'neutral-ov', 'openviking-plugin'] },
    { path: path.join(directory, '.gemini', 'relative') },
    { path: '~/personal' },
  ]);
  assert.deepEqual(plugins.inherits, [{ path: path.join(external, 'inherit-keep.json') }]);
  const skills = JSON.parse(fs.readFileSync(path.join(target, 'config', 'skills.json'), 'utf8'));
  assert.deepEqual(skills.entries, [{ path: path.join(external, 'skills'), exclude: ['openviking-skill'] }]);
});

test('Antigravity inherits: OpenViking arriving through an inherited file\'s entries is excluded and the rest stays', (t) => {
  const { directory, write, env } = agyHome(t);
  const external = path.join(directory, 'shared');
  write('shared/team/plugins/openviking-memory/plugin.json', { name: 'openviking-memory' });
  write('shared/team/plugins/control/plugin.json', { name: 'control' });
  write('shared/plugins.json', { entries: [{ path: 'team/plugins' }], inherits: [{ path: 'nested.json' }] });
  write('shared/nested.json', { entries: [{ path: 'team/plugins', exclude: ['legacy'] }], inherits: [{ path: 'plugins.json' }] });
  write('shared/clean.json', { entries: [{ path: '/opt/plugins' }] });
  // A file with no OpenViking of its own still inherits it from further down.
  write('shared/middle.json', { inherits: [{ path: 'plugins.json' }] });
  write('.gemini/config/plugins.json', { inherits: [
    { path: path.join(external, 'plugins.json') }, { path: path.join(external, 'clean.json') }, { path: path.join(external, 'middle.json') },
  ] });
  const target = prepareAgyGeminiDir(path.join(directory, 'vika-home'), env);
  const read = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));
  const top = read(path.join(target, 'config', 'plugins.json'));
  assert.equal(top.inherits[1].path, path.join(external, 'clean.json'), 'a clean inherit is not copied');
  assert.notEqual(top.inherits[0].path, path.join(external, 'plugins.json'));
  const copy = read(top.inherits[0].path);
  assert.deepEqual(copy.entries, [{ path: path.join(external, 'team/plugins'), exclude: ['openviking-memory'] }]);
  const nested = read(copy.inherits[0].path);
  assert.deepEqual(nested.entries, [{ path: path.join(external, 'team/plugins'), exclude: ['legacy', 'openviking-memory'] }]);
  assert.deepEqual(nested.inherits, [], 'the cycle back to the parent is cut');
  assert.notEqual(top.inherits[2].path, path.join(external, 'middle.json'));
  assert.notEqual(read(top.inherits[2].path).inherits[0].path, path.join(external, 'plugins.json'));
});

test('agyWorkspaceOpenVikingPieces walks every folder agy loads, up to the git root and no further', (t) => {
  const { directory, write } = home(t);
  write('outer/.agents/skills/openviking-above-git-root/SKILL.md', '#');
  write('outer/repo/.git/HEAD', 'ref');
  write('outer/repo/.agents/skills/openviking-rootskill/SKILL.md', '#');
  write('outer/repo/.agents/skills/control/SKILL.md', '#');
  write('outer/repo/sub/.agent/skills/openviking-a/SKILL.md', '#');
  write('outer/repo/sub/_agent/rules/openviking.md', '#');
  write('outer/repo/sub/_agents/skills/control/SKILL.md', '#');
  write('outer/repo/sub/_agents/plugins/openviking-plug/plugin.json', { name: 'x' });
  write('outer/repo/sub/.agents/skills.json', { entries: [{ path: '/opt/openviking/skills' }] });
  const repo = path.join(directory, 'outer/repo');
  assert.deepEqual(agyWorkspaceOpenVikingPieces(path.join(repo, 'sub')).sort(), [
    path.join(repo, '.agents/skills/openviking-rootskill'),
    path.join(repo, 'sub/.agent/skills/openviking-a'),
    path.join(repo, 'sub/.agents/skills.json'),
    path.join(repo, 'sub/_agent/rules/openviking.md'),
    path.join(repo, 'sub/_agents/plugins/openviking-plug'),
  ].sort());
  // Outside a git repository only the working directory itself counts.
  assert.deepEqual(agyWorkspaceOpenVikingPieces(path.join(directory, 'outer')), [path.join(directory, 'outer/.agents/skills/openviking-above-git-root')]);
});

test('agyWorkspaceOpenVikingPieces reports what a workspace would still load, and nothing else', (t) => {
  const { directory, write } = home(t);
  assert.deepEqual(agyWorkspaceOpenVikingPieces(path.join(directory, 'empty')), []);
  write('ws/.agents/mcp_config.json', { mcpServers: { openviking: { command: 'ov' } } });
  write('ws/.agents/hooks.json', { control: { Stop: [] } });
  write('ws/.agents/plugins/openviking-memory/plugin.json', { name: 'openviking-memory' });
  write('ws/.agents/plugins/control/plugin.json', { name: 'control' });
  write('ws/.agents/plugins/marketplace.json', { plugins: [{ name: 'openviking-memory' }] });
  const pieces = agyWorkspaceOpenVikingPieces(path.join(directory, 'ws'));
  assert.deepEqual(pieces, [
    path.join(directory, 'ws/.agents/mcp_config.json'),
    path.join(directory, 'ws/.agents/plugins/openviking-memory'),
  ]);
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
