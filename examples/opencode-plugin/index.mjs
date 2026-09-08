import { dirname } from "path"
import { fileURLToPath } from "url"
import { initializeRuntime } from "./lib/runtime.mjs"
import { createRepoContext } from "./lib/repo-context.mjs"
import { createMemorySessionManager } from "./lib/memory-session.mjs"
import { createMemoryRecall } from "./lib/memory-recall.mjs"
import { createSessionInject } from "./lib/session-inject.mjs"
import { createVikingUriGuard } from "./lib/viking-uri-guard.mjs"
import { injectOpenVikingMcpConfig } from "./lib/mcp-config.mjs"
import { loadConfig, resolveDataDir } from "./lib/config.mjs"
import { isRecallEnabled } from "./lib/shared/recall-core.mjs"
import { initLogger, log, makeToast } from "./lib/utils.mjs"

const pluginRoot = dirname(fileURLToPath(import.meta.url))

export async function OpenVikingPlugin({ client, directory }) {
  const config = loadConfig(pluginRoot, directory)
  const dataDir = resolveDataDir(pluginRoot, config)
  initLogger(dataDir)

  if (!config.enabled) {
    log("INFO", "plugin", "OpenViking plugin is disabled in configuration")
    return {}
  }

  const repoContext = createRepoContext({ config })
  const sessionManager = createMemorySessionManager({ config, pluginRoot: dataDir })
  const recall = createMemoryRecall({ config, sessionManager })
  const sessionInject = createSessionInject({ config, sessionManager })
  const vikingUriGuard = createVikingUriGuard()

  await sessionManager.init()
  const toast = makeToast(client)
  Promise.resolve().then(async () => {
    const ready = await initializeRuntime(config, client)
    if (ready) await repoContext.refreshRepos({ force: true })
  })

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

export default OpenVikingPlugin
