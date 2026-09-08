import path from "path"
import { homedir } from "os"
import { buildPluginConfig } from "./shared/plugin-config.mjs"

/**
 * Configuration for the opencode plugin.
 *
 * This plugin used to keep its own `openviking-config.json`, looked up along
 * four candidate paths. Every knob now comes from the same place as every other
 * harness — `ovcli.conf`'s `plugin` section, overridden by `plugin.opencode`,
 * by the workspace file, and by the environment — so `ov config switch` moves
 * behaviour along with credentials instead of only half of it.
 *
 * What stays here is the three knobs this plugin's own code reads as sections
 * rather than as flat keys.
 */
const MANIFEST_URL = new URL("../package.json", import.meta.url)

function expandHome(value) {
  if (!value || typeof value !== "string") return value
  if (value === "~") return homedir()
  if (value.startsWith("~/") || value.startsWith("~\\")) return path.join(homedir(), value.slice(2))
  return value
}

export function loadConfig(pluginRoot, projectDirectory) {
  const config = buildPluginConfig("opencode", {
    cwd: projectDirectory,
    manifestUrl: MANIFEST_URL,
    logFile: "opencode-plugin.log",
    deriveEffectivePeer: true,
  })

  return {
    ...config,
    // The MCP registration, the runtime data directory, and the repo context
    // cache.
    mcp: { enabled: config.mcpEnabled },
    runtime: { dataDir: config.dataDir },
    repoContext: { enabled: config.repoContext, cacheTtlMs: config.repoContextCacheTtlMs },
  }
}

export function resolveDataDir(pluginRoot, config) {
  const configured = config.runtime?.dataDir
  if (configured) return expandHome(configured)
  return path.join(homedir(), ".config", "opencode", "openviking")
}
