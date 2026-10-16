import { resolve as resolvePath } from "node:path"

export const OPENCODE_MCP_NAME = "openviking"

export function createOpenVikingMcpConfig(pluginRoot) {
  return {
    type: "local",
    command: ["node", resolvePath(pluginRoot, "servers", "mcp-proxy.mjs")],
    enabled: true,
    timeout: 15000,
  }
}

export function createOpenVikingV2McpConfig(pluginRoot) {
  return {
    type: "local",
    command: ["node", resolvePath(pluginRoot, "servers", "mcp-proxy.mjs")],
    codemode: false,
    timeout: {
      startup: 15000,
      catalog: 15000,
      execution: 15000,
    },
  }
}

export function openVikingSkillsDir(pluginRoot) {
  return resolvePath(pluginRoot, "skills")
}

// The bundled skills teach the MCP tools, so callers add them only once the
// server is registered.
export function injectOpenVikingSkillPaths(config, pluginRoot) {
  if (!config || typeof config !== "object") return false
  config.skills = config.skills && typeof config.skills === "object" ? config.skills : {}
  const paths = Array.isArray(config.skills.paths) ? config.skills.paths : []
  const dir = openVikingSkillsDir(pluginRoot)
  config.skills.paths = paths.includes(dir) ? paths : [...paths, dir]
  return true
}

export function injectOpenVikingMcpConfig(config, pluginRoot, enabled = true) {
  if (!config || typeof config !== "object") return false
  if (!enabled) return false
  config.mcp = config.mcp && typeof config.mcp === "object" ? config.mcp : {}
  const current = config.mcp[OPENCODE_MCP_NAME]
  if (current?.enabled === false) return false
  config.mcp[OPENCODE_MCP_NAME] = createOpenVikingMcpConfig(pluginRoot)
  return true
}
