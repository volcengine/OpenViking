import { AdapterError } from "./files.mjs";
import { normalizeURL } from "./config.mjs";

export async function detectBot(config, fetcher = fetch) {
  try {
    const response = await fetcher(`${config.serverUrl}/bot/v1/health`, {
      redirect: "error", headers: config.apiKey ? { "X-API-Key": config.apiKey } : {},
      signal: AbortSignal.timeout(config.timeoutMs),
    });
    if (response.status === 503) return { enabled: false, healthy: false, state: "disabled" };
    if (response.status === 404) return { enabled: false, healthy: false, state: "unsupported" };
    if (response.status === 502) return { enabled: true, healthy: false, state: "unhealthy" };
    if (!response.ok) return { enabled: false, healthy: false, state: "unknown" };
    const healthy = (await response.json()).status === "healthy";
    return { enabled: healthy, healthy, state: healthy ? "running" : "unknown" };
  } catch { return { enabled: false, healthy: false, state: "unknown" }; }
}

export function createAPI(config, fetcher = fetch) {
  return async (route, body, method) => {
    let response;
    try {
      response = await fetcher(`${config.serverUrl}${route}`, {
        method: method || (body === undefined ? "GET" : "PATCH"), redirect: "error",
        headers: { "Content-Type": "application/json", ...(config.apiKey ? { "X-API-Key": config.apiKey } : {}) },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }), signal: AbortSignal.timeout(config.timeoutMs),
      });
    } catch { throw new AdapterError("server_unreachable"); }
    if (response.status === 401) throw new AdapterError("root_authentication_required");
    if (response.status === 403) throw new AdapterError("root_permission_required");
    if (!response.ok) throw new AdapterError(`server_http_${response.status}`);
    let data;
    try { data = await response.json(); } catch { throw new AdapterError("invalid_server_response"); }
    if (data?.status !== "ok" || !data.result) throw new AdapterError("invalid_server_response");
    return data.result;
  };
}
export async function validateSelection(selection, config, fetcher = fetch) {
  if (!selection || typeof selection.model !== "string" || !selection.model.trim()
    || typeof selection.gateway?.key !== "string" || !selection.gateway.key.trim()) throw new AdapterError("incomplete_selection");
  const baseURL = normalizeURL(selection.gateway.baseURL);
  if (!baseURL.endsWith("/v1")) throw new AdapterError("invalid_gateway_base");
  // A catalog check spends no inference tokens. It also catches stale/deleted
  // models before any startup configuration is changed.
  let response;
  try {
    response = await fetcher(`${baseURL}/models`, { redirect: "error",
      headers: { Authorization: `Bearer ${selection.gateway.key}` }, signal: AbortSignal.timeout(config.timeoutMs) });
  } catch { throw new AdapterError("gateway_unreachable"); }
  if (!response.ok) throw new AdapterError(`gateway_http_${response.status}`);
  let catalog;
  try { catalog = await response.json(); } catch { throw new AdapterError("invalid_catalog"); }
  if (!Array.isArray(catalog?.data)) throw new AdapterError("invalid_catalog");
  for (const id of [selection.model, ...(config.botMode === "independent" ? [config.botModel || selection.model] : [])]) {
    const model = catalog.data.find((entry) => entry.id === id);
    if (!model) throw new AdapterError("model_not_available");
    const endpoints = model.native_endpoints;
    if (Array.isArray(endpoints) && endpoints.length && endpoints.every((endpoint) => /embeddings|rerank|audio|images|videos/.test(endpoint))) {
      throw new AdapterError("chat_model_required");
    }
  }
  const bot = catalog.data.find((entry) => entry.id === (config.botModel || selection.model));
  const vlm = catalog.data.find((entry) => entry.id === selection.model);
  const metadata = (entry, fallback = {}) => ({
    context: entry?.context_window ?? fallback.context,
    output: entry?.max_output_tokens ?? fallback.output,
    temperature: entry?.temperature,
  });
  return { ...selection, gateway: { baseURL, key: selection.gateway.key },
    vlmMetadata: metadata(vlm, selection.metadata),
    botMetadata: metadata(bot, config.botModel ? {} : selection.metadata) };
}
