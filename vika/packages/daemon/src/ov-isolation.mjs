// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

import { linkConfig } from './platform/index.mjs';

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

// Pi 1.4.0's built-ins. Keep the indirect MCP tools too: servers using
// codemode/deferred exposure need codemode/tool-search even though the Core's
// own tools have direct exposure.
export const PI_BUILTIN_EXTENSIONS = Object.freeze(['builtin:mcp', 'builtin:llama.cpp', 'builtin:codemode', 'builtin:tool-search']);

// Use Pi's resolver for every enabled extension: user/project discovery,
// settings paths, package manifests and their enable/disable filters. Then
// disable discovery and pass that set back without OpenViking. Merely walking
// agentDir/extensions loses configured/package/project extensions. Keeping the
// original agent directory also preserves models, MCP configuration, OAuth
// storage and its locks; no copied settings or credential symlinks are needed.
// The project is trusted here just as it is by Vika's Pi --approve argument.
export async function piIsolationArgs(env = process.env, workspace = process.cwd()) {
  const { DefaultPackageManager, SettingsManager } = await import('@earendil-works/pi-coding-agent');
  const agentDir = path.resolve(env.PI_CODING_AGENT_DIR || path.join(home(env), '.pi', 'agent'));
  const cwd = path.resolve(workspace);
  const settingsManager = SettingsManager.create(cwd, agentDir, { projectTrusted: true });
  const manager = new DefaultPackageManager({ cwd, agentDir, settingsManager,
    builtinExtensions: PI_BUILTIN_EXTENSIONS.map((entry) => entry.slice('builtin:'.length)) });
  const resolved = await manager.resolve();
  const extensions = resolved.extensions.filter((entry) => entry.enabled && !piOpenVikingExtension(entry, { agentDir, cwd }))
    .map((entry) => entry.path);
  // The Core requires Pi's MCP client, even when a user's standalone Pi has
  // disabled that built-in. Other extension enable/disable choices stay theirs.
  return ['-ne', ...uniq(['builtin:mcp', ...extensions]).flatMap((entry) => ['--extension', entry])];
}

function piOpenVikingExtension({ path: file, metadata }, { agentDir, cwd }) {
  if (file.startsWith('builtin:')) return false;
  const base = metadata.packageRoot || (metadata.scope === 'project' ? path.join(cwd, '.pi') : agentDir);
  const label = (value) => {
    const relative = path.relative(base, value);
    // Do not mistake an unrelated extension for OV because its checkout or
    // home happens to be named OpenViking. External paths retain their own
    // directory name (e.g. /opt/openviking/index.ts), not their ancestors.
    return relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)
      ? `${path.basename(path.dirname(value))}/${path.basename(value)}` : relative;
  };
  if (isOpenViking(label(file))) return true;
  // Configured paths can point straight at a checkout's generically named
  // entrypoint, without going through the `packages` setting.
  if (isOpenViking(piExtensionPackage(file))) return true;
  if (metadata.origin === 'package') {
    const source = metadata.source;
    if (isOpenViking(path.isAbsolute(source) || source.startsWith('.') ? path.basename(source) : source)) return true;
    if (isOpenViking(readJson(path.join(metadata.packageRoot ?? base, 'package.json'))?.name)) return true;
  }
  // An auto-discovered symlink may have a neutral name but point at OV.
  try { if (isOpenViking(label(fs.realpathSync(file)))) return true; } catch { /* Pi reports missing paths */ }
  return false;
}

function piExtensionPackage(file) {
  let directory;
  try {
    const real = fs.realpathSync(file);
    directory = fs.statSync(real).isDirectory() ? real : path.dirname(real);
  } catch { return null; }
  while (directory !== path.dirname(directory)) {
    const manifest = readJson(path.join(directory, 'package.json'));
    if (manifest) return manifest.name;
    directory = path.dirname(directory);
  }
  return null;
}

// OpenCode loads every file plugin in its global config folder whatever its
// config says, so the folder itself is replaced: XDG_CONFIG_HOME points at a
// Vika-owned directory that links every other entry of the user's config home
// back, and whose opencode/ holds the user's global config without
// OpenViking's plugins and MCP servers. The links keep every other tool the
// agent runs reading its usual configuration. On Windows, directory junctions
// need no symlink privilege; individual files are snapshots refreshed at launch.
// Returns the directory.
export function prepareOpencodeConfigHome(target, env = process.env) {
  const source = env.XDG_CONFIG_HOME || path.join(home(env), '.config');
  fs.rmSync(target, { recursive: true, force: true });
  fs.mkdirSync(path.join(target, 'opencode'), { recursive: true, mode: 0o700 });
  for (const entry of safeList(source)) {
    if (entry !== 'opencode') linkConfig(path.join(source, entry), path.join(target, entry));
  }
  const userFolder = path.join(source, 'opencode');
  for (const entry of safeList(userFolder)) {
    const from = path.join(userFolder, entry);
    const to = path.join(target, 'opencode', entry);
    if (/^opencode\.jsonc?$|^config\.json$/.test(entry)) continue;
    if (entry === 'plugin' || entry === 'plugins') {
      fs.mkdirSync(to, { recursive: true });
      for (const plugin of safeList(from)) if (!isOpenViking(plugin)) linkConfig(path.join(from, plugin), path.join(to, plugin));
      continue;
    }
    linkConfig(from, to);
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

// Antigravity CLI reads everything from --gemini-dir, so that directory is
// replaced the way OpenCode's config folder is. Measured on agy 1.5.3:
// - antigravity-cli holds the token, sessions and tool cache; it is linked
//   whole, so login and session/load keep their usual paths.
// - config/ is real: agy rewrites config.json, and a link would be replaced.
//   Files a plugin carries (hooks, MCP servers, rules, skills) go with it.
// - config/.migrated is always written. Without it agy migrates any legacy
//   antigravity-cli/{mcp_config,hooks}.json into config/: over the filtered
//   copies, and by moving the owner's files into this temporary directory.
// - plugins.json-style files register directories, so what they name is
//   filtered there too: an entry's path has no other way to be left out.
// HOME stays, so the tools agy runs keep their usual configuration and
// plugin secrets stay in ~/.config/antigravity. Returns the directory.
export function prepareAgyGeminiDir(target, env = process.env) {
  const source = path.join(home(env), '.gemini');
  const sourceConfig = path.join(source, 'config');
  const config = path.join(target, 'config');
  fs.rmSync(target, { recursive: true, force: true });
  fs.mkdirSync(config, { recursive: true, mode: 0o700 });
  for (const entry of safeList(source)) {
    if (entry !== 'config') linkConfig(path.join(source, entry), path.join(target, entry));
  }
  const write = (name, value) => fs.writeFileSync(path.join(config, name), JSON.stringify(value), { mode: 0o600 });
  fs.writeFileSync(path.join(config, '.migrated'), '', { mode: 0o600 });
  const mcp = readJson(path.join(sourceConfig, 'mcp_config.json')) ?? {};
  write('mcp_config.json', mcp.mcpServers ? { ...mcp, mcpServers: withoutOpenViking(mcp.mcpServers) } : mcp);
  write('hooks.json', withoutOpenViking(readJson(path.join(sourceConfig, 'hooks.json'))));
  const settings = readJson(path.join(sourceConfig, 'config.json')) ?? {};
  if (settings.plugins && typeof settings.plugins === 'object') {
    settings.plugins = Object.fromEntries(Object.entries(settings.plugins).filter(([name]) => !isOpenViking(name)));
  }
  write('config.json', settings);
  const items = {
    plugins: agyPluginIsOpenViking,
    skills: (_, name) => isOpenViking(name),
    rules: (_, name) => isOpenViking(name),
  };
  for (const [folder, isOv] of Object.entries(items)) {
    const from = path.join(sourceConfig, folder);
    fs.mkdirSync(path.join(config, folder), { recursive: true });
    for (const item of safeList(from)) {
      if (!isOv(path.join(from, item), item)) linkConfig(path.join(from, item), path.join(config, folder, item));
    }
  }
  const isOvItem = { 'plugins.json': agyPluginIsOpenViking };
  let inheritedCopies = 0;
  const copyInherited = (name, value) => {
    fs.mkdirSync(path.join(config, AGY_INHERITED_DIR), { recursive: true, mode: 0o700 });
    inheritedCopies += 1;
    const copy = path.join(AGY_INHERITED_DIR, `${inheritedCopies}-${path.basename(name)}`);
    write(copy, value);
    return path.join(config, copy);
  };
  for (const name of AGY_DECLARATIVE_CONFIGS) {
    const filtered = agyFilteredDeclaration(path.join(sourceConfig, name), isOvItem[name] ?? ((_, item) => isOpenViking(item)), env, copyInherited);
    if (filtered) write(name, filtered.declaration);
  }
  for (const entry of safeList(sourceConfig)) {
    if (!AGY_MANAGED_CONFIG.has(entry) && !isOpenViking(entry)) linkConfig(path.join(sourceConfig, entry), path.join(config, entry));
  }
  return target;
}

const AGY_DECLARATIVE_CONFIGS = ['plugins.json', 'skills.json', 'rules.json', 'agents.json'];
const AGY_INHERITED_DIR = 'vika-inherited';
// `.migrated` and `projects` are agy's own bookkeeping for this home; the
// marker is written fresh above.
const AGY_MANAGED_CONFIG = new Set([
  'mcp_config.json', 'hooks.json', 'config.json', 'plugins', 'skills', 'rules', '.migrated', 'projects',
  ...AGY_DECLARATIVE_CONFIGS,
]);
const AGY_PLUGIN_MANIFESTS = [
  'plugin.json', '.antigravity-plugin/plugin.json', '.gemini-plugin/plugin.json', '.claude-plugin/plugin.json', 'gemini-extension.json',
];

const withoutOpenViking = (entries) => Object.fromEntries(Object.entries(entries ?? {})
  .filter(([name, value]) => !isOpenViking(name) && !isOpenViking(JSON.stringify(value))));

// A plugin is OpenViking's by its name, what its folder links to, its manifest,
// or the MCP servers and hooks it brings, whatever the folder is called.
function agyPluginIsOpenViking(directory, name) {
  if (isOpenViking(name)) return true;
  try { if (isOpenViking(path.basename(fs.realpathSync(directory)))) return true; } catch { /* a broken link is no plugin */ }
  if (AGY_PLUGIN_MANIFESTS.some((file) => isOpenViking(readJson(path.join(directory, file))?.name))) return true;
  return ['mcp_config.json', 'hooks.json'].some((file) => {
    const contents = readJson(path.join(directory, file));
    return contents && isOpenViking(JSON.stringify(contents));
  });
}

// The last two segments, as for Pi above: an entry is not OpenViking's because
// its checkout's ancestors happen to be named so.
function agyPathNamesOpenViking(file) {
  const label = (value) => `${path.basename(path.dirname(value))}/${path.basename(value)}`;
  if (isOpenViking(label(file))) return true;
  try { return isOpenViking(label(fs.realpathSync(file))); } catch { return false; }
}

// One plugins.json-style file. An entry names a directory scanned one level
// deep and an inherits item another such file; both can bring OpenViking in
// from outside config/. Relative paths are the owner's config/ folder's, so
// they are rewritten: this copy lives elsewhere. An inherited file that brings
// OpenViking only through its own entries is replaced by a filtered copy, so
// the rest of what it declares still loads. Returns the filtered declaration
// and whether anything was left out.
function agyFilteredDeclaration(file, isOvItem, env, copyInherited, ancestors = new Set()) {
  const declaration = readJson(file);
  if (!declaration || typeof declaration !== 'object') return null;
  const chain = new Set(ancestors).add(path.resolve(file));
  let changed = false;
  const base = path.dirname(file);
  const resolved = (value) => value.startsWith('~/') ? path.join(home(env), value.slice(2)) : path.resolve(base, value);
  const located = (value) => value.startsWith('~/') ? value : path.resolve(base, value);
  const usable = (entry) => typeof entry?.path === 'string' && !agyPathNamesOpenViking(resolved(entry.path));
  const result = { ...declaration };
  if (Array.isArray(declaration.entries)) {
    const kept = declaration.entries.filter(usable);
    if (kept.length !== declaration.entries.length) changed = true;
    result.entries = kept.map((entry) => {
      const directory = resolved(entry.path);
      const excluded = safeList(directory).filter((item) => isOvItem(path.join(directory, item), item));
      if (excluded.length > 0) changed = true;
      return {
        ...entry,
        path: located(entry.path),
        ...(excluded.length > 0
          ? { exclude: [...(Array.isArray(entry.exclude) ? entry.exclude : []), ...excluded.map(escapePattern)] }
          : {}),
      };
    });
  }
  if (Array.isArray(declaration.inherits)) {
    result.inherits = [];
    for (const entry of declaration.inherits) {
      const target = usable(entry) ? resolved(entry.path) : null;
      // An inherit cycle adds nothing its ancestors have not already declared.
      if (!target || isOpenViking(read(target)) || chain.has(path.resolve(target))) {
        changed = true;
        continue;
      }
      const inherited = agyFilteredDeclaration(target, isOvItem, env, copyInherited, chain);
      if (inherited?.changed) changed = true;
      result.inherits.push({
        ...entry,
        path: inherited?.changed ? copyInherited(target, inherited.declaration) : located(entry.path),
      });
    }
  }
  return { declaration: result, changed };
}

const escapePattern = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

// agy also loads these folders at every level from the working directory up to
// its git root, and has no switch for them. Vika's own workspace has none, so a
// piece found there is warned about, not refused; the OPENVIKING_* variables
// keep it inert meanwhile. Only reads.
const AGY_WORKSPACE_FOLDERS = ['.agents', '.agent', '_agents', '_agent'];
const AGY_WORKSPACE_FILES = ['mcp_config.json', 'hooks.json', ...AGY_DECLARATIVE_CONFIGS];

function agyWorkspaceLevels(workspace) {
  const levels = [];
  let directory = workspace;
  for (;;) {
    levels.push(directory);
    if (fs.existsSync(path.join(directory, '.git'))) return levels;
    const parent = path.dirname(directory);
    if (parent === directory) return [workspace];
    directory = parent;
  }
}

export function agyWorkspaceOpenVikingPieces(workspace) {
  if (!workspace) return [];
  const pieces = [];
  for (const level of agyWorkspaceLevels(path.resolve(workspace))) {
    for (const folder of AGY_WORKSPACE_FOLDERS) {
      const agents = path.join(level, folder);
      for (const name of AGY_WORKSPACE_FILES) {
        const file = path.join(agents, name);
        if (isOpenViking(read(file))) pieces.push(file);
      }
      const plugins = path.join(agents, 'plugins');
      for (const entry of safeList(plugins)) {
        const directory = path.join(plugins, entry);
        if (agyPluginIsOpenViking(directory, entry)) pieces.push(directory);
      }
      for (const kind of ['skills', 'rules']) {
        for (const entry of safeList(path.join(agents, kind))) {
          if (isOpenViking(entry)) pieces.push(path.join(agents, kind, entry));
        }
      }
    }
  }
  return pieces;
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
