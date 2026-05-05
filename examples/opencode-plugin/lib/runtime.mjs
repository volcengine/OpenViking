import fs from "fs"
import path from "path"
import { spawn } from "child_process"
import { homedir } from "os"
import { log, makeToast, normalizeEndpoint } from "./utils.mjs"

export async function checkServiceHealth(config, timeoutMs = 3000) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${normalizeEndpoint(config.endpoint)}/health`, {
      method: "GET",
      signal: controller.signal,
    })
    return response.ok
  } catch (error) {
    log("WARN", "health", "OpenViking health check failed", {
      endpoint: config.endpoint,
      error: error?.message,
    })
    return false
  } finally {
    clearTimeout(timeout)
  }
}

export async function initializeRuntime(config, client, pluginRoot) {
  const toast = makeToast(client)

  if (await checkServiceHealth(config)) {
    log("INFO", "runtime", "OpenViking service is healthy", { endpoint: config.endpoint })
    return true
  }

  if (!config.runtime?.autoStartServer) {
    await toast(`OpenViking service is not reachable at ${config.endpoint}. Start openviking-server before using memory tools.`, "warning")
    return false
  }

  const ovConf = path.join(homedir(), ".openviking", "ov.conf")
  if (!fs.existsSync(ovConf)) {
    await toast("~/.openviking/ov.conf not found. Configure OpenViking before enabling autoStartServer.", "warning")
    return false
  }

  try {
    await startServer(ovConf, pluginRoot)
    for (let i = 0; i < 10; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 3000))
      if (await checkServiceHealth(config)) {
        log("INFO", "runtime", "OpenViking server started", { config: ovConf })
        return true
      }
    }
  } catch (error) {
    log("ERROR", "runtime", "Failed to start OpenViking server", { error: error?.message })
  }

  await toast("Failed to start OpenViking server. Check openviking-server.log in the plugin directory.", "error")
  return false
}

async function startServer(ovConf, pluginRoot) {
  fs.mkdirSync(pluginRoot, { recursive: true })
  const logPath = path.join(pluginRoot, "openviking-server.log")
  const out = fs.openSync(logPath, "a")
  const child = spawn("openviking-server", ["--config", ovConf], {
    detached: true,
    stdio: ["ignore", out, out],
    windowsHide: true,
    shell: process.platform === "win32",
  })
  child.unref()
}

