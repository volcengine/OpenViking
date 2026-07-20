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
  const recall = createMemoryRecall({ config })
  const sessionInject = createSessionInject({ config, sessionManager })
  const vikingUriGuard = createVikingUriGuard()

  await sessionManager.init()
  const toast = makeToast(client)
  if (config.legacyCredentialsUsed) {
    log("WARN", "config", "Legacy OpenCode credential fields are still in use; run node scripts/setup.mjs to migrate to ovcli.conf", {
      configPath: config.configPath,
    })
    await toast("OpenViking credentials in openviking-config.json are deprecated. Run node scripts/setup.mjs to migrate.", "warning")
  }

  Promise.resolve().then(async () => {
    const ready = await initializeRuntime(config, client)
    if (ready) await repoContext.refreshRepos({ force: true })
  })

  return {
    config: async (opencodeConfig) => {
      const injected = injectOpenVikingMcpConfig(opencodeConfig, pluginRoot)
      log(injected ? "INFO" : "WARN", "mcp", injected ? "Registered OpenViking MCP server" : "OpenViking MCP server was not registered")
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
        if (!config.autoRecall?.enabled) return
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
