import { createRepoContext } from "./repo-context.mjs"
import { createMemorySessionManager } from "./memory-session.mjs"
import { createMemoryRecall } from "./memory-recall.mjs"
import { createSessionInject } from "./session-inject.mjs"
import { createVikingUriGuard, createVikingUriNotice } from "./viking-uri-guard.mjs"
import { loadConfig, resolveDataDir } from "./config.mjs"
import { initializeRuntime } from "./runtime.mjs"
import { initLogger, log } from "./utils.mjs"

export function createOpenVikingRuntime({ client, directory, pluginRoot }) {
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

  // OpenCode v2 has no v1 client object and waits for setup before accepting
  // the first prompt. Load local state before hooks run, but keep health and
  // pending replay off that activation path.
  const ready = sessionManager.init({ deferNetwork: !client })
  const background = ready.then(async () => {
    const healthy = await initializeRuntime(config, client)
    if (healthy) await repoContext.refreshRepos({ force: true })
  }).catch((error) => {
    log("WARN", "plugin", "OpenViking runtime initialization failed", {
      error: error?.message ?? String(error),
    })
  })

  return {
    config,
    repoContext,
    sessionManager,
    recall,
    sessionInject,
    vikingUriGuard,
    vikingUriNotice,
    ready,
    background,
  }
}
