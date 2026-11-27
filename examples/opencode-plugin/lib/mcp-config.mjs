import { resolve as resolvePath } from "node:path"

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
