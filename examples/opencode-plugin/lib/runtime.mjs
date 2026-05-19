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

export async function initializeRuntime(config, client) {
  const toast = makeToast(client)

  if (await checkServiceHealth(config)) {
    log("INFO", "runtime", "OpenViking service is healthy", { endpoint: config.endpoint })
    return true
  }

  await toast(`OpenViking service is not reachable at ${config.endpoint}. Start openviking-server before using memory tools.`, "warning")
  return false
}
