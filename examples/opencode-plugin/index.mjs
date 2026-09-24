import { dirname } from "path"
import { fileURLToPath } from "url"
import { createOpenVikingRuntime } from "./lib/plugin-runtime.mjs"
import { startV2Plugin } from "./lib/v2-plugin.mjs"
import { injectOpenVikingMcpConfig } from "./lib/mcp-config.mjs"
import { isRecallEnabled } from "./lib/shared/recall-core.mjs"
import { log } from "./lib/utils.mjs"

const pluginRoot = dirname(fileURLToPath(import.meta.url))

export async function OpenVikingPlugin({ client, directory } = {}) {
  const runtime = createOpenVikingRuntime({ client, directory, pluginRoot })
  if (!runtime) return {}
  await runtime.ready
  return v1Hooks(runtime)
}

function v1Hooks(runtime) {
  const {
    config,
    sessionManager,
    repoContext,
    recall,
    sessionInject,
    vikingUriGuard,
    vikingUriNotice,
  } = runtime

  return {
    config: async (opencodeConfig) => {
      const injected = injectOpenVikingMcpConfig(opencodeConfig, pluginRoot, config.mcp.enabled)
      const hookOnly = !config.mcp.enabled
      log(
        injected || hookOnly ? "INFO" : "WARN",
        "mcp",
        injected ? "Registered OpenViking MCP server" :
          hookOnly ? "Skipped bundled MCP registration in hook-only mode" :
            "OpenViking MCP server was not registered",
      )
    },

    event: async ({ event }) => {
      await sessionManager.handleEvent(event)
      if (event?.type === "session.created") {
        await repoContext.refreshRepos({ force: true })
      }
    },

    "tool.execute.before": vikingUriGuard,
    "tool.execute.after": vikingUriNotice,

    "experimental.chat.system.transform": (_input, output) => {
      const prompt = repoContext.getRepoSystemPrompt()
      if (prompt) output.system.push(prompt)
    },

    "chat.message": async (input, output) => {
      try {
        await sessionInject.injectSessionContext(input, output)
        if (!isRecallEnabled(config)) return
        await recall.injectRelevantMemories(input, output)
      } catch (error) {
        log("WARN", "recall", "Auto recall failed", { error: error?.message ?? String(error) })
      }
    },

    "experimental.session.compacting": async (input) => {
      log("INFO", "compaction", "OpenCode session compacting", {
        opencode_session: input.sessionID,
      })
      await sessionManager.flushSession(input.sessionID, {
        commit: true,
        reason: "experimental.session.compacting",
      })
    },

    dispose: async () => {
      await sessionManager.flushAll({ commit: true })
      log("INFO", "plugin", "OpenViking plugin disposed")
    },
  }
}

const OpenVikingV2Plugin = {
  id: "openviking",
  async setup(ctx) {
    const directory = ctx?.location?.project?.directory || ctx?.location?.directory
    const runtime = createOpenVikingRuntime({ directory, pluginRoot })
    if (!runtime) return
    return startV2Plugin(ctx, runtime, { pluginRoot })
  },
  async server(input) {
    return OpenVikingPlugin(input)
  },
}

export default OpenVikingV2Plugin
