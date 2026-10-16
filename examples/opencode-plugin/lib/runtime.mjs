import { fetchJSON, getResponseErrorMessage, log, makeToast } from "./utils.mjs"

// The probe used to send a User-Agent and nothing else, so a server that wants
// a key reported itself as down and the plugin disabled itself on startup.
export async function checkServiceHealth(config, timeoutMs = 3000) {
  const res = await fetchJSON(config, "/health", { method: "GET" }, { timeoutMs })
  if (res.ok) return true

  log("WARN", "health", "OpenViking health check failed", {
    endpoint: config.endpoint,
    status: res.status,
    error: getResponseErrorMessage(res.error),
  })
  return false
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
