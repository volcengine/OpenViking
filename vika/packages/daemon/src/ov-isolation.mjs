// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// Vika owns OpenViking for the agents it runs (ov-session-context.mjs,
// ov-recall.mjs, the Core's openviking_* tools). A runtime's own OpenViking
// plugin would add a second tool surface that speaks as the whole process,
// with no idea who sent a line, plus hooks that recall and capture on their
// own. So every local runtime is launched with that plugin switched off, by
// the runtime's own launch arguments or environment: never by asking the
// plugin, whose version Vika does not control. Only OpenViking's pieces go;
// the owner's other plugins, hooks and MCP servers stay.
//
// Each runtime declares its mechanism in the runtime catalog (`ovIsolation`),
// and a new runtime must declare one before it can be registered; how to find
// and verify one is in docs/openviking-ownership.md. The user's
// own configuration is only ever read here, never written.

// The official plugin id, whatever the user's files say.
export const OV_PLUGIN_ID = 'openviking-memory@openviking';

// Whether a plugin, MCP server or extension is OpenViking's.
export const isOpenViking = (text) => /openviking/i.test(String(text ?? ''));

const read = (file) => {
  try { return fs.readFileSync(file, 'utf8'); } catch { return ''; }
};
const readJson = (file) => {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
};
const home = (env) => env.HOME || os.homedir();
const uniq = (values) => [...new Set(values)];

// A TOML file's tables of one kind ([plugins."id"] or [mcp_servers.name]),
// each with its own body. Enough for the names and the command line; nothing
// else of the file is interpreted.
function tomlTables(text, kind) {
  const tables = [];
  let current = null;
  for (const line of String(text).split('\n')) {
    const header = /^\s*\[([^\]]+)\]\s*$/.exec(line);
    if (header) {
      const match = new RegExp(`^${kind}\\.(?:"([^"]+)"|([^.\\s"]+))$`).exec(header[1].trim());
      current = match ? { name: match[1] ?? match[2], body: '' } : null;
      if (current) tables.push(current);
    } else if (current) {
      current.body += `${line}\n`;
    }
  }
  return tables;
}

// Codex and TraeX read one TOML config (CODEX_HOME/config.toml,
// TRAE_HOME/traecli.toml). Off go every OpenViking plugin, every MCP server
// that names or runs OpenViking, and the runtime's own memory feature: Vika's
// memory is OpenViking, through the daemon. Plugin hooks go with the plugin;
// a hand-written hooks.json entry for OpenViking stays inert through the
// OPENVIKING_* process variables (deployment-config.cjs).
export function codexIsolationArgs(configFile) {
  const text = read(configFile);
  const plugins = uniq([OV_PLUGIN_ID, ...tomlTables(text, 'plugins').map(({ name }) => name).filter(isOpenViking)]);
  const servers = tomlTables(text, 'mcp_servers').filter(({ name, body }) => isOpenViking(name) || isOpenViking(body))
    .map(({ name }) => name).filter((name) => /^[A-Za-z0-9_-]+$/.test(name));
  return [
    ...plugins.flatMap((id) => ['-c', `plugins.${id}.enabled=false`]),
    ...servers.flatMap((name) => ['-c', `mcp_servers.${name}.enabled=false`]),
    '-c', 'features.memories=false',
  ];
}

export const codexConfigFile = (env = process.env) =>
  path.join(env.CODEX_HOME || path.join(home(env), '.codex'), 'config.toml');
export const traexConfigFile = (env = process.env) =>
  path.join(env.TRAE_HOME || path.join(home(env), '.trae'), 'traecli.toml');

// Claude Code: one --settings layer that disables every OpenViking plugin the
// user enabled (their hooks and MCP servers with them) and denies every user
// or project MCP server that names or runs OpenViking. The daemon's own
// personal_agent server comes from --mcp-config and is never OpenViking's.
export function claudeIsolationSettings({ env = process.env, workspace = null } = {}) {
  const configDir = env.CLAUDE_CONFIG_DIR || path.join(home(env), '.claude');
  const settings = readJson(path.join(configDir, 'settings.json')) ?? {};
  const plugins = uniq([OV_PLUGIN_ID, ...Object.keys(settings.enabledPlugins ?? {}).filter(isOpenViking)]);
  const state = readJson(env.CLAUDE_CONFIG_DIR ? path.join(env.CLAUDE_CONFIG_DIR, '.claude.json') : path.join(home(env), '.claude.json')) ?? {};
  const project = workspace ? readJson(path.join(workspace, '.mcp.json')) ?? {} : {};
  const serverSets = [state.mcpServers, workspace ? state.projects?.[workspace]?.mcpServers : null, project.mcpServers];
  const servers = uniq(['openviking', ...serverSets.flatMap((set) => Object.entries(set ?? {})
    .filter(([name, server]) => isOpenViking(name) || isOpenViking(JSON.stringify(server))).map(([name]) => name))]);
  return {
    enabledPlugins: Object.fromEntries(plugins.map((id) => [id, false])),
    deniedMcpServers: servers.filter((name) => name !== 'personal_agent').map((serverName) => ({ serverName })),
  };
}

// Pi discovers extensions from its agent directory and ships built-ins; the
// OpenViking extension is one of the discovered. Discovery is switched off
// and everything but OpenViking's is loaded again by path: the built-ins Pi
// documents and each extension in the agent directory's extensions folder.
// Extensions installed as Pi packages are not loaded for Vika's instances.
// The short flag on purpose: Pi 1.4.0 ignores the long --no-extensions once an
// --extension follows (or precedes) it, and discovers everything again; -ne
// holds (verified against the real CLI, test/pi-mcp.integration.test.mjs).
export const PI_BUILTIN_EXTENSIONS = Object.freeze(['builtin:mcp', 'builtin:llama.cpp']);
export function piIsolationArgs(env = process.env) {
  const agentDir = env.PI_CODING_AGENT_DIR || path.join(home(env), '.pi', 'agent');
  const folder = path.join(agentDir, 'extensions');
  let entries = [];
  try {
    entries = fs.readdirSync(folder, { withFileTypes: true })
      .filter((entry) => !entry.name.startsWith('.') && !isOpenViking(entry.name)
        && (entry.isDirectory() || /\.(?:[cm]?[jt]s)$/.test(entry.name)))
      .map((entry) => path.join(folder, entry.name));
  } catch { /* no extensions folder */ }
  return ['-ne', ...[...PI_BUILTIN_EXTENSIONS, ...entries].flatMap((extension) => ['--extension', extension])];
}

// OpenCode loads every file plugin in its global config folder whatever its
// config says, so the folder itself is replaced: XDG_CONFIG_HOME points at a
// Vika-owned directory that links every other entry of the user's config home
// back, and whose opencode/ holds the user's global config without
// OpenViking's plugins and MCP servers. The links keep every other tool the
// agent runs reading its usual configuration. Returns the directory.
export function prepareOpencodeConfigHome(target, env = process.env) {
  const source = env.XDG_CONFIG_HOME || path.join(home(env), '.config');
  fs.rmSync(target, { recursive: true, force: true });
  fs.mkdirSync(path.join(target, 'opencode'), { recursive: true, mode: 0o700 });
  const link = (from, to) => { try { fs.symlinkSync(from, to); } catch { /* raced or unsupported: skip */ } };
  for (const entry of safeList(source)) {
    if (entry !== 'opencode') link(path.join(source, entry), path.join(target, entry));
  }
  const userFolder = path.join(source, 'opencode');
  for (const entry of safeList(userFolder)) {
    const from = path.join(userFolder, entry);
    const to = path.join(target, 'opencode', entry);
    if (/^opencode\.jsonc?$|^config\.json$/.test(entry)) continue;
    if (entry === 'plugin' || entry === 'plugins') {
      fs.mkdirSync(to, { recursive: true });
      for (const plugin of safeList(from)) if (!isOpenViking(plugin)) link(path.join(from, plugin), path.join(to, plugin));
      continue;
    }
    link(from, to);
  }
  const config = ['opencode.json', 'opencode.jsonc', 'config.json'].map((name) => path.join(userFolder, name))
    .map((file) => readJsonc(file)).find(Boolean) ?? {};
  for (const key of ['plugin', 'plugins']) {
    if (Array.isArray(config[key])) config[key] = config[key].filter((plugin) => !isOpenViking(plugin));
  }
  if (config.mcp && typeof config.mcp === 'object') {
    config.mcp = Object.fromEntries(Object.entries(config.mcp)
      .filter(([name, server]) => !isOpenViking(name) && !isOpenViking(JSON.stringify(server))));
  }
  fs.writeFileSync(path.join(target, 'opencode', 'opencode.json'), JSON.stringify(config), { mode: 0o600 });
  return target;
}

function safeList(folder) {
  try { return fs.readdirSync(folder); } catch { return []; }
}

// OpenCode configs may be JSONC: comments and trailing commas.
function readJsonc(file) {
  const text = read(file);
  if (!text) return null;
  try {
    return JSON.parse(text.replace(/("(?:\\.|[^"\\])*")|\/\/[^\n]*|\/\*[\s\S]*?\*\//g, (match, string) => string ?? '')
      .replace(/,(\s*[}\]])/g, '$1'));
  } catch {
    return null;
  }
}
