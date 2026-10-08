#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

function readJson(file) {
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

function ownsHook(value) {
  return JSON.stringify(value || {}).includes("openviking-memory");
}

function ownsMcpServer(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  if (value.env?.OPENVIKING_INTEGRATION_ID === "openviking-memory") return true;
  const text = JSON.stringify(value);
  return text.includes("agent-integrations") && text.includes("mcp-proxy.mjs");
}

function requireObject(value, label) {
  if (value === undefined) return {};
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`Cannot safely update Qoder settings: ${label} must be an object`);
  }
  return value;
}

export function writeQoderSettings({ settingsPath, root, nodeBin, sourceMode }) {
  const hostDir = path.join(root, "hosts", "qoder");
  const packageManifest = readJson(path.join(hostDir, "openviking.integration.json"));
  if (packageManifest.id !== "openviking-memory"
    || !Array.isArray(packageManifest.clients)
    || !packageManifest.clients.includes("qoder")) {
    throw new Error("Invalid OpenViking integration manifest for qoder");
  }

  const settings = readJson(settingsPath);
  settings.hooks = requireObject(settings.hooks, "hooks");
  settings.mcpServers = requireObject(settings.mcpServers, "mcpServers");
  if (settings.mcpServers.openviking && !ownsMcpServer(settings.mcpServers.openviking)) {
    throw new Error("mcpServers.openviking already exists and is not managed by OpenViking");
  }

  const integrationEnv = {
    OPENVIKING_INTEGRATION_ID: packageManifest.id,
    OPENVIKING_INTEGRATION_VERSION: packageManifest.version,
    OPENVIKING_HOOK_SOURCE: "qoder",
  };
  const envPrefix = Object.entries(integrationEnv)
    .map(([key, value]) => `${key}=${shellArg(value)}`)
    .join(" ");
  const renderHookValue = (value) => {
    if (Array.isArray(value)) return value.map(renderHookValue);
    if (!value || typeof value !== "object") return value;
    return Object.fromEntries(Object.entries(value).map(([key, child]) => {
      if (key !== "command" || typeof child !== "string") return [key, renderHookValue(child)];
      const match = /^node\s+"?__OPENVIKING_PLUGIN_ROOT__\/([^\s"]+)"?(\s.*)?$/u.exec(child);
      if (!match) throw new Error(`Unsupported qoder hook command template: ${child}`);
      const args = (match[2] || "").replaceAll("__OPENVIKING_CLIENT_ID__", "qoder");
      const command = `${shellArg(nodeBin)} ${shellArg(path.join(root, match[1]))}${args}`;
      return [key, `${envPrefix} ${command} # openviking-memory`];
    }));
  };

  const hookTemplate = readJson(path.join(hostDir, "hooks.json"));
  const templateHooks = requireObject(hookTemplate.hooks, "hook template");
  for (const [event, entries] of Object.entries(templateHooks)) {
    if (!Array.isArray(entries)) throw new Error(`Invalid qoder hook entries for ${event}`);
    const current = Array.isArray(settings.hooks[event]) ? settings.hooks[event] : [];
    settings.hooks[event] = [
      ...current.filter((item) => !ownsHook(item)),
      ...renderHookValue(entries),
    ];
  }

  const mcpTemplate = readJson(path.join(hostDir, ".mcp.json"));
  const templateServer = mcpTemplate.mcpServers?.openviking;
  if (!templateServer || typeof templateServer !== "object" || Array.isArray(templateServer)) {
    throw new Error("Invalid qoder MCP template");
  }
  settings.mcpServers.openviking = {
    ...templateServer,
    command: nodeBin,
    args: [path.join(root, "servers", "mcp-proxy.mjs")],
    env: { ...(templateServer.env || {}), ...integrationEnv },
  };
  atomicWrite(settingsPath, settings);

  const installedManifestPath = path.join(root, "integration.json");
  const previousManifest = readJson(installedManifestPath);
  const now = new Date().toISOString();
  const unchangedInstall = previousManifest.version === packageManifest.version
    && previousManifest.source === sourceMode
    && previousManifest.hooksConfig === settingsPath
    && previousManifest.mcpConfig === settingsPath;
  atomicWrite(installedManifestPath, {
    schemaVersion: 1,
    id: packageManifest.id,
    version: packageManifest.version,
    client: "qoder",
    installMode: "managed-native",
    source: sourceMode,
    capabilities: packageManifest.capabilities,
    hooksConfig: settingsPath,
    mcpConfig: settingsPath,
    installedAt: previousManifest.installedAt || now,
    updatedAt: unchangedInstall ? previousManifest.updatedAt || previousManifest.installedAt || now : now,
  });
}

export function removeQoderSettings({ settingsPath }) {
  if (!fs.existsSync(settingsPath)) return;
  const settings = readJson(settingsPath);
  if (settings.hooks && typeof settings.hooks === "object" && !Array.isArray(settings.hooks)) {
    for (const event of Object.keys(settings.hooks)) {
      if (!Array.isArray(settings.hooks[event])) continue;
      settings.hooks[event] = settings.hooks[event].filter((item) => !ownsHook(item));
      if (settings.hooks[event].length === 0) delete settings.hooks[event];
    }
  }
  if (ownsMcpServer(settings.mcpServers?.openviking)) {
    delete settings.mcpServers.openviking;
  }
  atomicWrite(settingsPath, settings, { backup: false });
  fs.rmSync(`${settingsPath}.bak`, { force: true });
}

if (process.argv[1]
  && fs.realpathSync(process.argv[1]) === fs.realpathSync(fileURLToPath(import.meta.url))) {
  const [command, settingsPath, root, nodeBin, sourceMode] = process.argv.slice(2);
  if (command === "write") {
    writeQoderSettings({ settingsPath, root, nodeBin, sourceMode });
  } else if (command === "remove") {
    removeQoderSettings({ settingsPath });
  } else {
    throw new Error("Usage: settings-config.mjs <write|remove> <settings-path> [root node-bin source-mode]");
  }
}
