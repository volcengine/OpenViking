import { createRepoContext } from "./repo-context.mjs"
import { createMemorySessionManager } from "./memory-session.mjs"
import { createMemoryRecall } from "./memory-recall.mjs"
import { createSessionInject } from "./session-inject.mjs"
import { createVikingUriGuard, createVikingUriNotice } from "./viking-uri-guard.mjs"
import { loadConfig, resolveDataDir } from "./config.mjs"
import { initializeRuntime } from "./runtime.mjs"
import { initLogger, log } from "./utils.mjs"

export async function createOpenVikingRuntime({ client, directory, pluginRoot }) {
  const config = loadConfig(pluginRoot, directory)
  const dataDir = resolveDataDir(pluginRoot, config)
  initLogger(dataDir)

  if (!config.enabled) {
    log("INFO", "plugin", "OpenViking plugin is disabled in configuration")
    return null
  }

  const repoContext = createRepoContext({ config })
  const sessionManager = createMemorySessionManager({ config, pluginRoot: dataDir })
  const recall = createMemoryRecall({ config, sessionManager })
  const sessionInject = createSessionInject({ config, sessionManager })
  const vikingUriGuard = createVikingUriGuard()
  const vikingUriNotice = createVikingUriNotice()

  await sessionManager.init()
  Promise.resolve().then(async () => {
    const ready = await initializeRuntime(config, client)
    if (ready) await repoContext.refreshRepos({ force: true })
  })

  return {
    config,
    repoContext,
    sessionManager,
    recall,
    sessionInject,
    vikingUriGuard,
    vikingUriNotice,
  }
}
