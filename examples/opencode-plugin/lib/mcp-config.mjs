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

export function injectOpenVikingMcpConfig(config, pluginRoot) {
  if (!config || typeof config !== "object") return false
  config.mcp = config.mcp && typeof config.mcp === "object" ? config.mcp : {}
  const current = config.mcp[OPENCODE_MCP_NAME]
  if (current?.enabled === false) return false
  config.mcp[OPENCODE_MCP_NAME] = createOpenVikingMcpConfig(pluginRoot)
  return true
}
