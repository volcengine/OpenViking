#!/usr/bin/env node
/**
 * The hooks/mcp merge behind every config-driven host.
 *
 * cursor, trae, trae-cn and zcode keep their hooks and MCP servers in JSON the
 * user edits too, so installing, uninstalling and folding the two files into
 * zcode's single config are the same three problems each time: read a file
 * without clobbering what could not be parsed, decide which entries are this
 * installer's, and write back atomically. Three copies of that had already
 * drifted — the uninstall side learned to reclaim the URI-guard entries and the
 * install side never did, so a rename left a stale guard hook behind.
 *
 * Installer-only: nothing under lib/ imports it, so it stays out of the runtime
 * closure sync.mjs vendors into the plugins.
 */

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

/**
 * The file's top-level object, `{}` when it is absent or empty.
 *
 * Anything else — unreadable, malformed, not an object — throws, because the
 * only thing every caller does next is write the file back.
 */
export function readJson(file) {
  let raw;
  try {
    raw = fs.readFileSync(file, "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return {};
    throw new Error(`Cannot safely update ${file}: ${error.message}`);
  }
  if (!raw.trim()) return {};
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new Error(`Cannot safely update ${file}: ${error.message}`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error(`Cannot safely update ${file}: top-level value must be an object`);
  }
  return parsed;
}

function atomicWrite(file, value, { backup = true } = {}) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const next = JSON.stringify(value, null, 2) + "\n";
  let previous = "";
  try { previous = fs.readFileSync(file, "utf8"); } catch {}
  if (previous === next) return;
  if (previous && backup) fs.writeFileSync(`${file}.bak`, previous, { mode: 0o600 });
  const tmp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(tmp, next, { mode: 0o600 });
  fs.renameSync(tmp, file);
}

function shellArg(value) {
  return `'${String(value).replace(/'/g, `'"'"'`)}'`;
}

// Everything this installer writes today carries the integration id. The script
// names are how an entry from an older install, written before that env prefix
// existed, still gets reclaimed instead of accumulating beside the new one.
const OPENVIKING_HOOK_SCRIPTS = [
  "hook.mjs",
  "hook-entry.mjs",
  "session-start.mjs",
  "auto-recall.mjs",
  "auto-capture.mjs",
  "pre-compact.mjs",
  "session-end.mjs",
  "trae-auto-recall.mjs",
  "trae-auto-capture.mjs",
  "trae-cli-hook.mjs",
  "uri-guard.mjs",
  "claude-code-memory-plugin/scripts/session-start.mjs",
];

/** Whether a host's hook entry is one this installer owns. */
export function ownsHook(value) {
  const text = JSON.stringify(value || {});
  return text.includes("OPENVIKING_INTEGRATION_ID")
    || (text.includes("openviking") && OPENVIKING_HOOK_SCRIPTS.some((name) => text.includes(name)));
}

function isKnownLegacyOpenVikingServer(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (value.env?.OPENVIKING_INTEGRATION_ID === "openviking-memory") return true;
  if (typeof value.url !== "string") return false;
  try {
    const url = new URL(value.url);
    const local = ["127.0.0.1", "localhost", "::1"].includes(url.hostname)
      && url.port === "1933" && url.pathname.replace(/\/$/u, "") === "/mcp";
    const cloud = url.hostname === "api.vikingdb.cn-beijing.volces.com"
      && url.pathname.replace(/\/$/u, "") === "/openviking/mcp";
    return local || cloud;
  } catch {
    return false;
  }
}

/** Render the host's hook and MCP templates into the user's own config files. */
export function writeHostJsonConfigs({ kind, hooksPath, mcpPath, root, clientId, nodeBin, sourceMode }) {
  // One plugin serves every config-driven host; `kind` names the host directory
  // this client's configuration templates live in.
  const hostDir = path.join(root, "hosts", kind);
  const packageManifest = readJson(path.join(hostDir, "openviking.integration.json"));
  if (packageManifest.id !== "openviking-memory" || !Array.isArray(packageManifest.clients)
    || !packageManifest.clients.includes(clientId)) {
    throw new Error(`Invalid OpenViking integration manifest for ${clientId}`);
  }
  const integrationEnv = {
    OPENVIKING_INTEGRATION_ID: packageManifest.id,
    OPENVIKING_INTEGRATION_VERSION: packageManifest.version,
    OPENVIKING_HOOK_SOURCE: clientId,
  };
  const envPrefix = Object.entries(integrationEnv)
    .map(([key, value]) => `${key}=${shellArg(value)}`)
    .join(" ");

  function renderHookCommand(command) {
    const match = /^node\s+"?__OPENVIKING_PLUGIN_ROOT__\/([^\s"]+)"?(\s.*)?$/u.exec(command);
    if (!match) throw new Error(`Unsupported ${clientId} hook command template: ${command}`);
    const args = (match[2] || "").replaceAll("__OPENVIKING_CLIENT_ID__", clientId);
    const rendered = `${shellArg(nodeBin)} ${shellArg(path.join(root, match[1]))}${args}`;
    return `${envPrefix} ${rendered} # openviking-memory`;
  }

  function renderHookValue(value) {
    if (Array.isArray(value)) return value.map(renderHookValue);
    if (!value || typeof value !== "object") return value;
    return Object.fromEntries(Object.entries(value).map(([key, child]) => [
      key,
      key === "command" && typeof child === "string" ? renderHookCommand(child) : renderHookValue(child),
    ]));
  }

  const hookTemplate = readJson(path.join(hostDir, "hooks.json"));
  if (!hookTemplate.hooks || typeof hookTemplate.hooks !== "object" || Array.isArray(hookTemplate.hooks)) {
    throw new Error(`Invalid ${clientId} hooks template`);
  }
  const hooksConfig = readJson(hooksPath);
  hooksConfig.version = Number.isFinite(Number(hooksConfig.version)) ? Number(hooksConfig.version) : 1;
  hooksConfig.hooks = hooksConfig.hooks && typeof hooksConfig.hooks === "object" && !Array.isArray(hooksConfig.hooks)
    ? hooksConfig.hooks : {};

  for (const [event, entries] of Object.entries(hookTemplate.hooks)) {
    if (!Array.isArray(entries)) throw new Error(`Invalid ${clientId} hook entries for ${event}`);
    const current = Array.isArray(hooksConfig.hooks[event]) ? hooksConfig.hooks[event] : [];
    hooksConfig.hooks[event] = [
      ...current.filter((item) => !ownsHook(item)),
      ...renderHookValue(entries),
    ];
  }
  if (kind === "cursor") {
    if (Array.isArray(hooksConfig.hooks.postToolUse)) {
      const remaining = hooksConfig.hooks.postToolUse.filter((item) => !ownsHook(item));
      if (remaining.length) hooksConfig.hooks.postToolUse = remaining;
      else delete hooksConfig.hooks.postToolUse;
    }
  }
  atomicWrite(hooksPath, hooksConfig);

  const mcpTemplate = readJson(path.join(hostDir, ".mcp.json"));
  const templateServer = mcpTemplate.mcpServers?.openviking;
  if (!templateServer || typeof templateServer !== "object" || Array.isArray(templateServer)) {
    throw new Error(`Invalid ${clientId} MCP template`);
  }
  const mcp = readJson(mcpPath);
  mcp.mcpServers = mcp.mcpServers && typeof mcp.mcpServers === "object" && !Array.isArray(mcp.mcpServers)
    ? mcp.mcpServers : {};
  // Migrate only the exact OpenViking endpoints published by the earlier manual
  // guides. A coincidentally named third-party server must remain untouched.
  if (isKnownLegacyOpenVikingServer(mcp.mcpServers["ov-mcp-server"])) {
    delete mcp.mcpServers["ov-mcp-server"];
  }
  mcp.mcpServers.openviking = {
    ...templateServer,
    command: nodeBin,
    args: [path.join(root, "servers", "mcp-proxy.mjs")],
    env: { ...(templateServer.env || {}), ...integrationEnv },
  };
  atomicWrite(mcpPath, mcp);

  const installedManifestPath = path.join(root, "integration.json");
  const previousManifest = readJson(installedManifestPath);
  const now = new Date().toISOString();
  const unchangedInstall = previousManifest.version === packageManifest.version
    && previousManifest.source === sourceMode
    && previousManifest.hooksConfig === hooksPath
    && previousManifest.mcpConfig === mcpPath;
  atomicWrite(installedManifestPath, {
    schemaVersion: 1,
    id: packageManifest.id,
    version: packageManifest.version,
    client: clientId,
    installMode: "managed-native",
    source: sourceMode,
    capabilities: packageManifest.capabilities,
    hooksConfig: hooksPath,
    mcpConfig: mcpPath,
    installedAt: previousManifest.installedAt || now,
    updatedAt: unchangedInstall ? previousManifest.updatedAt || previousManifest.installedAt || now : now,
  });
}

/**
 * Drop this installer's hook entries and MCP server from the user's config files.
 *
 * The one caller with nothing to protect: a backup taken here would hold the
 * entries the uninstall just removed, so these writes take none and the copy an
 * install left behind goes too. `mcpPath` may be empty for a host that keeps no
 * MCP file of its own.
 */
export function removeHostJsonConfigs({ hooksPath, mcpPath }) {
  const hooks = fs.existsSync(hooksPath) ? readJson(hooksPath) : null;
  const mcp = mcpPath && fs.existsSync(mcpPath) ? readJson(mcpPath) : null;
  if (hooks?.hooks && typeof hooks.hooks === "object") {
    for (const event of Object.keys(hooks.hooks)) {
      if (!Array.isArray(hooks.hooks[event])) continue;
      hooks.hooks[event] = hooks.hooks[event].filter((item) => !ownsHook(item));
      if (hooks.hooks[event].length === 0) delete hooks.hooks[event];
    }
    atomicWrite(hooksPath, hooks, { backup: false });
  }
  if (mcp?.mcpServers?.openviking) {
    const text = JSON.stringify(mcp.mcpServers.openviking);
    if (text.includes("agent-integrations") && text.includes("mcp-proxy.mjs")) {
      delete mcp.mcpServers.openviking;
      atomicWrite(mcpPath, mcp, { backup: false });
    }
  }
  for (const file of [hooksPath, mcpPath]) {
    if (file) fs.rmSync(`${file}.bak`, { force: true });
  }
}

/**
 * Fold the rendered hooks.json and mcp.json into zcode's single config.json.
 *
 * ZCode reads hooks from `config.json` → `hooks.events` and MCP servers from
 * `mcp.servers`, so the files `writeHostJsonConfigs` produced are an
 * intermediate step for this host rather than the thing it loads.
 */
export function mergeZcodeConfig({ configPath, hooksPath, mcpPath }) {
  const config = readJson(configPath);

  if (fs.existsSync(hooksPath)) {
    const hooks = readJson(hooksPath);
    config.hooks = config.hooks || {};
    config.hooks.enabled = true;
    config.hooks.events = config.hooks.events || {};
    for (const [event, handlers] of Object.entries(hooks.hooks || {})) {
      const existing = (config.hooks.events[event] || []).filter((group) => !ownsHook(group));
      config.hooks.events[event] = [...existing, ...handlers];
    }
  }

  if (fs.existsSync(mcpPath) && fs.statSync(mcpPath).size > 0) {
    const mcp = readJson(mcpPath);
    const incoming = { ...(mcp.mcpServers || {}) };
    if (mcp.openviking) incoming.openviking = mcp.openviking;
    config.mcp = config.mcp || {};
    config.mcp.servers = config.mcp.servers || {};
    for (const [name, server] of Object.entries(incoming)) {
      const existing = config.mcp.servers[name];
      // Only replace an entry that does not exist yet or is already ours.
      if (existing && !JSON.stringify(existing).includes("openviking-memory")) {
        process.stderr.write(`Skipping ${name} MCP server: already exists and is not managed by OpenViking\n`);
        continue;
      }
      config.mcp.servers[name] = server;
    }
  }

  atomicWrite(configPath, config);
}

const COMMANDS = {
  write: ([kind, hooksPath, mcpPath, root, clientId, nodeBin, sourceMode]) =>
    writeHostJsonConfigs({ kind, hooksPath, mcpPath, root, clientId, nodeBin, sourceMode }),
  remove: ([hooksPath, mcpPath]) => removeHostJsonConfigs({ hooksPath, mcpPath }),
  "merge-zcode": ([configPath, hooksPath, mcpPath]) => mergeZcodeConfig({ configPath, hooksPath, mcpPath }),
};

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const [command, ...argv] = process.argv.slice(2);
  const run = COMMANDS[command];
  if (!run) {
    process.stderr.write(`usage: host-json-config.mjs <${Object.keys(COMMANDS).join("|")}> ...\n`);
    process.exit(2);
  }
  try {
    run(argv);
  } catch (error) {
    process.stderr.write(`${error?.message || error}\n`);
    process.exit(1);
  }
}
