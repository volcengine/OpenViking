import { dirname } from "path"
import { fileURLToPath } from "url"
import { loadConfig, resolveDataDir } from "./lib/config.mjs"
import { startV2Plugin } from "./lib/v2-plugin.mjs"
import { initLogger, log } from "./lib/utils.mjs"

const pluginRoot = dirname(fileURLToPath(import.meta.url))

// OpenCode 1.x loads the plugin through `server()` or this named export.
export async function OpenVikingPlugin({ directory } = {}) {
  try {
    initLogger(resolveDataDir(pluginRoot, loadConfig(pluginRoot, directory)))
  } catch {}
  log("WARN", "plugin", "OpenCode 1.x is no longer supported by this version of the OpenViking plugin; it does nothing. Upgrade to OpenCode 2.")
  return {}
}

const OpenVikingV2Plugin = {
  id: "openviking",
  async setup(ctx) {
    return startV2Plugin(ctx, { pluginRoot })
  },
  async server(input) {
    return OpenVikingPlugin(input)
  },
}

export default OpenVikingV2Plugin
