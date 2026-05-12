import { dirname } from "path"
import { fileURLToPath } from "url"
import { initializeRuntime } from "./lib/runtime.mjs"
import { createRepoContext } from "./lib/repo-context.mjs"
import { createMemorySessionManager } from "./lib/memory-session.mjs"
import { createMemoryTools } from "./lib/memory-tools.mjs"
import { createMemoryRecall } from "./lib/memory-recall.mjs"
import { initLogger, loadConfig, log, resolveDataDir } from "./lib/utils.mjs"

const pluginRoot = dirname(fileURLToPath(import.meta.url))

/**
 * @type {import('@opencode-ai/plugin').Plugin}
 */
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
  const tools = createMemoryTools({ config, sessionManager, projectDirectory: directory })

  await sessionManager.init()

  Promise.resolve().then(async () => {
    const ready = await initializeRuntime(config, client)
    if (ready) await repoContext.refreshRepos({ force: true })
  })

  return {
    event: async ({ event }) => {
      await sessionManager.handleEvent(event)
      if (event?.type === "session.created") {
        await repoContext.refreshRepos({ force: true })
      }
    },

    tool: tools,

    "experimental.chat.system.transform": (_input, output) => {
      const prompt = repoContext.getRepoSystemPrompt()
      if (prompt) output.system.push(prompt)
    },

    "chat.message": async (input, output) => {
      try {
        if (!config.autoRecall?.enabled) return
        await recall.injectRelevantMemories(input, output)
      } catch (error) {
        log("WARN", "recall", "Auto recall failed", { error: error?.message ?? String(error) })
      }
    },

    stop: async () => {
      await sessionManager.flushAll({ commit: true })
      log("INFO", "plugin", "OpenViking plugin stopped")
    },
  }
}

export default OpenVikingPlugin
