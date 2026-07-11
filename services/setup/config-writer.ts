import * as fs from "node:fs";
import * as path from "node:path";

export type SlotActivationResult = {
  activated: boolean;
  previousOwner?: string;
  replaced: boolean;
};

export type SetupIO = {
  readConfig: (configPath: string) => Record<string, unknown>;
  writeConfig: (configPath: string, config: Record<string, unknown>) => void;
  backupConfig: (configPath: string) => string | null;
};

export const defaultSetupIO: SetupIO = {
  readConfig: readOpenClawConfig,
  writeConfig: writeOpenClawConfigFile,
  backupConfig,
};

export function readOpenClawConfig(configPath: string): Record<string, unknown> {
  if (!fs.existsSync(configPath)) return {};
  try {
    return JSON.parse(fs.readFileSync(configPath, "utf-8"));
  } catch {
    return {};
  }
}

function writeOpenClawConfigFile(configPath: string, config: Record<string, unknown>): void {
  const configDir = path.dirname(configPath);
  if (!fs.existsSync(configDir)) fs.mkdirSync(configDir, { recursive: true });
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n", "utf-8");
}

export function backupConfig(configPath: string): string | null {
  if (!fs.existsSync(configPath)) return null;
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const backupPath = `${configPath}.bak.${timestamp}`;
  try {
    fs.copyFileSync(configPath, backupPath);
    return backupPath;
  } catch {
    return null;
  }
}

export function getExistingPluginConfig(config: Record<string, unknown>): Record<string, unknown> | null {
  const plugins = config.plugins as Record<string, unknown> | undefined;
  if (!plugins) return null;
  const entries = plugins.entries as Record<string, unknown> | undefined;
  if (!entries) return null;
  const entry = entries.openviking as Record<string, unknown> | undefined;
  if (!entry) return null;
  const cfg = entry.config as Record<string, unknown> | undefined;
  return cfg && cfg.mode ? cfg : null;
}

export function ensureInstallRecord(plugins: Record<string, unknown>): void {
  const installs = plugins.installs as Record<string, unknown> | undefined;
  if (installs && typeof installs === "object") {
    delete installs.openviking;
  }

  if (!plugins.allow) plugins.allow = [];
  const allow = plugins.allow as string[];
  if (!allow.includes("openviking")) {
    allow.push("openviking");
  }
}

export function writeOpenVikingConfig(
  configPath: string,
  pluginCfg: Record<string, unknown>,
  io: SetupIO = defaultSetupIO,
): void {
  io.backupConfig(configPath);

  const config = io.readConfig(configPath);

  if (!config.plugins) config.plugins = {};
  const plugins = config.plugins as Record<string, unknown>;
  if (!plugins.entries) plugins.entries = {};
  const entries = plugins.entries as Record<string, unknown>;

  const existingEntry = (entries.openviking as Record<string, unknown>) ?? {};
  entries.openviking = { ...existingEntry, config: pluginCfg };

  ensureInstallRecord(plugins);

  io.writeConfig(configPath, config);
}

export function activateContextEngineSlot(
  configPath: string,
  force: boolean,
  io: SetupIO = defaultSetupIO,
): SlotActivationResult {
  const config = io.readConfig(configPath);
  if (!config.plugins) config.plugins = {};
  const plugins = config.plugins as Record<string, unknown>;
  if (!plugins.slots) plugins.slots = {};
  const slots = plugins.slots as Record<string, unknown>;

  const current = slots.contextEngine as string | undefined;

  if (current === "openviking") return { activated: false, replaced: false };

  if (current && current !== "openviking" && !force) {
    return { activated: false, previousOwner: current, replaced: false };
  }

  slots.contextEngine = "openviking";
  io.writeConfig(configPath, config);
  return { activated: true, previousOwner: current || undefined, replaced: !!current };
}

export function isContextEngineSlotActive(configPath: string, io: SetupIO = defaultSetupIO): boolean {
  const config = io.readConfig(configPath);
  const plugins = config.plugins as Record<string, unknown> | undefined;
  if (!plugins) return false;
  const slots = plugins.slots as Record<string, unknown> | undefined;
  return slots?.contextEngine === "openviking";
}
