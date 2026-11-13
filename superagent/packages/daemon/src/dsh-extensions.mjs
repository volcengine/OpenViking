// Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
// SPDX-License-Identifier: AGPL-3.0

// Declarative DSH extensions for one instance.
//
// Setup selects the standard skill roots and the runtime writes that selection
// to the patch. This module adds operator-authored custom skill directories and
// user MCP servers, which in DSH are Cordis rows rather than a settings
// document. It reads a small JSON document from the instance and workspace,
// validates it, and hands the runtime the values for its own `--patch` overlay.
// It never writes DSH's files and never installs a plugin.
//
// Global:  <dataDir>/dsh/extensions.json
// Project: <workspace>/.agents/dsh.json
//
// Both files use the same shape:
//   {
//     "hostSkills": true,
//     "skillDirs": ["D:/shared/skills"],
//     "mcpServers": {
//       "matlab": { "command": "D:/tools/matlab-mcp.exe", "args": ["--serve"] }
//     }
//   }
//
// Project values win per MCP server name; custom skill roots are concatenated
// after setup's selected standard roots. The retired hostSkills flag no longer
// widens that selection.

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const DSH_EXTENSIONS_FILE = 'extensions.json';
export const DSH_PROJECT_EXTENSIONS_FILE = 'dsh.json';

// dsh-mcp-client rejects anything outside this pattern; validating here turns a
// broken file into one skipped server instead of a DSH process that cannot start.
const MCP_SERVER_NAME = /^[A-Za-z0-9_-]{1,32}$/;
// The daemon owns this namespace for its own tool server.
export const DSH_RESERVED_MCP_SERVERS = Object.freeze(['personal_agent']);

// The host user's own skill roots, relative to the home directory of the account
// that runs the daemon. DSH reads both from its user-level roots when it runs
// without an isolated home; scanning them as custom roots keeps that behaviour.
const HOST_SKILL_SEGMENTS = Object.freeze([
  Object.freeze(['.agents', 'skills']),
  Object.freeze(['.dsh', 'skills']),
]);

/**
 * The two documents read for one instance.
 * @param {object} options
 * @param {string} options.dataDir - instance data directory.
 * @param {string} options.workspace - instance workspace.
 * @returns {{global: string, project: string}} absolute paths.
 */
export function dshExtensionsPaths({ dataDir, workspace } = {}) {
  if (typeof dataDir !== 'string' || !dataDir) throw new Error('dsh dataDir is required');
  if (typeof workspace !== 'string' || !workspace) throw new Error('dsh workspace is required');
  return {
    global: path.join(dataDir, 'dsh', DSH_EXTENSIONS_FILE),
    project: path.join(workspace, '.agents', DSH_PROJECT_EXTENSIONS_FILE),
  };
}

/**
 * Host user skill roots: `~/.agents/skills` and `~/.dsh/skills`.
 *
 * They are returned even when the directories do not exist: DSH probes missing
 * roots and picks them up once they appear, so a skill installed while the
 * instance is running needs no configuration change.
 * @param {object} [options]
 * @param {string} [options.home] - the host account's home directory.
 * @returns {string[]} absolute paths, `~/.agents/skills` first.
 */
export function defaultHostSkillDirs({ home = os.homedir() } = {}) {
  if (typeof home !== 'string' || !home) throw new Error('home is required');
  return HOST_SKILL_SEGMENTS.map(segments => path.join(home, ...segments));
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function readDocument(file, warn) {
  let text;
  try {
    text = fs.readFileSync(file, 'utf8');
  } catch (error) {
    if (error?.code !== 'ENOENT') warn(`cannot read ${file}: ${error.message}`);
    return null;
  }
  try {
    const parsed = JSON.parse(text);
    if (!isRecord(parsed)) {
      warn(`${file}: expected a JSON object`);
      return null;
    }
    return parsed;
  } catch (error) {
    warn(`${file}: ${error.message}`);
    return null;
  }
}

function expandHome(value, home) {
  if (value === '~') return home;
  if (value.startsWith('~/') || value.startsWith('~\\')) return path.join(home, value.slice(2));
  return value;
}

function resolveSkillDir(value, base, home) {
  const expanded = expandHome(String(value), home);
  return path.isAbsolute(expanded) ? path.normalize(expanded) : path.resolve(base, expanded);
}

function stringArray(value, field, warn) {
  if (value === undefined) return [];
  if (!Array.isArray(value)) {
    warn(`${field} must be an array of non-empty strings`);
    return [];
  }
  const result = [];
  for (const item of value) {
    if (typeof item === 'string' && item) result.push(item);
    else warn(`${field} must contain only non-empty strings`);
  }
  return result;
}

function optionalPositiveNumber(value, field, warn) {
  if (value === undefined) return undefined;
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
    warn(`${field} must be a positive number`);
    return undefined;
  }
  return value;
}

function optionalBoolean(value, field, warn) {
  if (value === undefined) return undefined;
  if (typeof value !== 'boolean') {
    warn(`${field} must be a boolean`);
    return undefined;
  }
  return value;
}

function stringMap(value, field, warn) {
  if (value === undefined) return undefined;
  if (!isRecord(value) || Object.values(value).some(item => typeof item !== 'string')) {
    warn(`${field} must be an object of strings`);
    return undefined;
  }
  return { ...value };
}

/**
 * Turn one user entry into exactly the config `@deepseek-ai/dsh-mcp-client`
 * accepts. Unknown keys are dropped rather than forwarded: a typo must not
 * become a DSH startup failure.
 * @param {string} name - the serverName.
 * @param {unknown} value - the entry as written.
 * @param {Function} warn - diagnostic sink.
 * @returns {object|null} the validated config, or null when the entry is skipped.
 */
export function normalizeMcpServer(name, value, warn = () => {}) {
  if (!MCP_SERVER_NAME.test(name)) {
    warn(`mcpServers.${name}: name must match ${MCP_SERVER_NAME}`);
    return null;
  }
  if (DSH_RESERVED_MCP_SERVERS.includes(name)) {
    warn(`mcpServers.${name}: this name is reserved by OpenViking SuperAgent`);
    return null;
  }
  if (!isRecord(value)) {
    warn(`mcpServers.${name}: expected an object`);
    return null;
  }
  const transport = value.transport ?? (value.url !== undefined ? 'streamable-http' : 'stdio');
  const common = {
    serverName: name,
    toolCallTimeoutMs: optionalPositiveNumber(value.toolCallTimeoutMs, `mcpServers.${name}.toolCallTimeoutMs`, warn),
    failOnStartupError: optionalBoolean(value.failOnStartupError, `mcpServers.${name}.failOnStartupError`, warn),
    maxInstructionBytes: optionalPositiveNumber(value.maxInstructionBytes, `mcpServers.${name}.maxInstructionBytes`, warn),
  };
  for (const key of Object.keys(common)) if (common[key] === undefined) delete common[key];

  if (transport === 'stdio') {
    if (typeof value.command !== 'string' || !value.command) {
      warn(`mcpServers.${name}: stdio needs a command`);
      return null;
    }
    const args = value.args === undefined ? [] : value.args;
    if (!Array.isArray(args) || args.some(item => typeof item !== 'string')) {
      warn(`mcpServers.${name}: args must be an array of strings`);
      return null;
    }
    const env = stringMap(value.env, `mcpServers.${name}.env`, warn);
    if (value.env !== undefined && env === undefined) return null;
    return {
      transport, ...common, command: value.command, args: [...args],
      ...(env ? { env } : {}),
      ...(typeof value.cwd === 'string' && value.cwd ? { cwd: value.cwd } : {}),
    };
  }
  if (transport === 'streamable-http') {
    if (typeof value.url !== 'string' || !value.url) {
      warn(`mcpServers.${name}: streamable-http needs a url`);
      return null;
    }
    const headers = stringMap(value.headers, `mcpServers.${name}.headers`, warn);
    if (value.headers !== undefined && headers === undefined) return null;
    return { transport, ...common, url: value.url, ...(headers ? { headers } : {}) };
  }
  warn(`mcpServers.${name}: transport must be stdio or streamable-http`);
  return null;
}

function mergeDocuments(documents, home, warn) {
  const mcpServers = new Map();
  const configured = [];
  let hostSkills = false;
  for (const { file, base, document } of documents) {
    if (!document) continue;
    const localWarn = message => warn(`${file}: ${message}`);
    for (const directory of stringArray(document.skillDirs, 'skillDirs', localWarn)) {
      const resolved = resolveSkillDir(directory, base, home);
      if (!configured.includes(resolved)) configured.push(resolved);
    }
    if (document.mcpServers !== undefined) {
      if (!isRecord(document.mcpServers)) {
        localWarn('mcpServers must be an object');
      } else {
        for (const [name, value] of Object.entries(document.mcpServers)) {
          const server = normalizeMcpServer(name, value, localWarn);
          if (server) mcpServers.set(name, server);
        }
      }
    }
    if (document.hostSkills !== undefined && typeof document.hostSkills !== 'boolean') {
      localWarn('hostSkills must be a boolean');
    }
  }
  return { mcpServers: [...mcpServers.values()], configuredSkillDirs: configured, hostSkills };
}

/**
 * Read and merge the instance and workspace extension documents.
 *
 * A missing file is normal. A broken file, an invalid server, or an invalid
 * field is reported through `warn` and skipped; it never blocks the DSH
 * process from starting.
 * @param {object} options
 * @param {string} options.dataDir - instance data directory.
 * @param {string} options.workspace - instance workspace.
 * @param {string} [options.home] - host home for the default skill roots.
 * @param {Function} [options.warn] - receives one human-readable line per problem.
 * @returns {{mcpServers: object[], skillDirs: string[], hostSkills: boolean}}
 *   `skillDirs` contains only custom roots from the two documents.
 */
export function readDshExtensions({
  dataDir, workspace, home = os.homedir(), warn = () => {},
} = {}) {
  const paths = dshExtensionsPaths({ dataDir, workspace });
  const merged = mergeDocuments([
    { file: paths.global, base: path.join(dataDir, 'dsh'), document: readDocument(paths.global, warn) },
    { file: paths.project, base: workspace, document: readDocument(paths.project, warn) },
  ], home, warn);
  return { mcpServers: merged.mcpServers, skillDirs: merged.configuredSkillDirs, hostSkills: merged.hostSkills };
}
