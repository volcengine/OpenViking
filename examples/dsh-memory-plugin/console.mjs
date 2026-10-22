/**
 * The browser half of the bundle opens the OpenViking Studio inside DSH.
 *
 * The Studio is a separate SPA with its own router and CSS. A cross-origin
 * iframe could not be themed by the plugin: browsers do not let a page read
 * another origin's DOM. The plugin therefore proxies the Studio through its own
 * Host and serves it from the DSH origin:
 *
 *   /studio/*              ->  <OpenViking console>/studio/*   (HTML/assets)
 *   /openviking/console    ->  status/config JSON              (fenced)
 *
 * The proxied HTML gets a bootstrap that points the Studio's API connection at
 * the plugin's configured endpoint, so API calls do not go to the DSH origin.
 * Because the iframe is same-origin, the browser half reads the DSH theme tokens
 * and applies them to the Studio's CSS variables directly. The Studio's own
 * theme toggle is overridden only inside the embedded panel.
 */

/** The fenced status route the browser half reads. */
export const CONSOLE_ROUTE = "/openviking/console";

/** Prefix the Studio is proxied under on the DSH origin. */
export const STUDIO_PROXY_PATH = "/studio";

/** The proxied Studio entry page. */
export const STUDIO_PAGE = "/studio/home";

/** Local servers answer immediately; a slow proxy gets one budget, not a hang. */
export const CONSOLE_TIMEOUT_MS = 2500;

/** HTML/assets can be larger than a health probe; still bounded. */
const PROXY_TIMEOUT_MS = 20000;

const LOCAL_ENDPOINT = "http://127.0.0.1:1933";

/**
 * Keep only an http(s) URL, without credentials. The path keeps no trailing
 * slash (so a base and the same base with `/` derive one Studio URL), while
 * query and hash survive because a managed console may need them.
 */
export function normalizeConsoleUrl(value) {
  const raw = typeof value === "string" ? value.trim() : "";
  if (!raw) return undefined;
  let url;
  try {
    url = new URL(raw);
  } catch {
    return undefined;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return undefined;
  url.username = "";
  url.password = "";
  if (url.pathname.length > 1) url.pathname = url.pathname.replace(/\/+$/, "");
  return url.href;
}

/**
 * Turn the configured console address into the Studio entry page. A base gets
 * `/studio/home` appended (a reverse-proxied deployment keeps its prefix), a
 * `/studio` base gets `/home`, and an explicit page URL is left alone.
 */
export function resolveStudioUrl(value) {
  const normalized = normalizeConsoleUrl(value);
  if (!normalized) return undefined;
  const url = new URL(normalized);
  const path = url.pathname.length > 1 ? url.pathname.replace(/\/+$/, "") : "";
  if (/\/studio\/home$/i.test(path)) {
    url.pathname = path;
    return url.href;
  }
  if (/\/studio$/i.test(path)) {
    url.pathname = `${path}/home`;
    return url.href;
  }
  url.search = "";
  url.hash = "";
  url.pathname = `${path}/studio/home`;
  return url.href;
}

/**
 * The base the proxy fetches the Studio from. A `consoleUrl` may name the
 * console base, a `/studio` base, or a full `/studio/home` page; strip the
 * Studio suffix so the proxied `/studio/*` path is appended exactly once.
 */
export function proxyTargetBase(value) {
  const normalized = normalizeConsoleUrl(value);
  if (!normalized) return LOCAL_ENDPOINT;
  const url = new URL(normalized);
  url.search = "";
  url.hash = "";
  url.pathname = url.pathname.replace(/\/studio(?:\/home)?\/?$/i, "");
  return url.href.replace(/\/+$/, "");
}

/** Build the upstream URL for one proxied request. */
export function buildStudioTarget(config = {}, pathname = STUDIO_PROXY_PATH, search = "") {
  const base = proxyTargetBase(config.consoleUrl || config.baseUrl);
  const path = pathname.startsWith(STUDIO_PROXY_PATH) ? pathname : `${STUDIO_PROXY_PATH}${pathname}`;
  return `${base}${path}${search}`;
}

/**
 * Point the proxied Studio at the configured API endpoint before its bundle
 * runs. The API key is deliberately absent: the Studio authenticates in the
 * browser with its own connection settings, and this plugin never puts the key
 * in the page. `theme=system` leaves the host palette in charge of the scheme.
 */
export function injectConsoleBootstrap(html, apiBase) {
  const profile = {
    activeId: "dsh-console",
    profiles: [{
      id: "dsh-console",
      name: "OpenViking (DSH)",
      connection: {
        accountId: "default",
        userId: "default",
        apiKey: "",
        adminApiKey: "",
        baseUrl: apiBase,
        serviceSelection: "opensource",
      },
    }],
  };
  const script = `<script>(() => { try { localStorage.setItem("theme", "system"); localStorage.setItem("ov_console_connection_profiles", ${JSON.stringify(JSON.stringify(profile))}); } catch {} })();</script>`;
  return html.includes("<head>") ? html.replace("<head>", `<head>${script}`) : `${script}${html}`;
}

/**
 * Proxy the Studio's static HTML/assets through the DSH origin. The route is
 * public static content (no key, no session) and is not fenced: the embedded
 * frame's navigation must be able to load it. The proxy target is fixed by the
 * plugin's own config, so it cannot be used to reach arbitrary hosts.
 */
export function createStudioProxy({ config = {}, fetchImpl, timeoutMs = PROXY_TIMEOUT_MS } = {}) {
  const apiBase = normalizeConsoleUrl(config.baseUrl) || LOCAL_ENDPOINT;
  return async function studioProxy(req, res) {
    if (req.method !== "GET" && req.method !== "HEAD") {
      writeText(res, 405, "method not allowed");
      return;
    }
    const url = new URL(req.url || "/", "http://dsh.internal");
    const pathname = decodeURIComponent(url.pathname);
    if (!pathname.startsWith(STUDIO_PROXY_PATH)) {
      writeText(res, 404, "not found");
      return;
    }
    let response;
    try {
      response = await (fetchImpl || globalThis.fetch)(buildStudioTarget(config, pathname, url.search), {
        method: req.method,
        redirect: "follow",
        signal: AbortSignal.timeout(timeoutMs),
        headers: { accept: req.headers.accept || "*/*" },
      });
    } catch (error) {
      writeText(res, 502, `OpenViking Studio proxy failed: ${messageOf(error)}`);
      return;
    }
    const contentType = String(response.headers.get("content-type") || "");
    if (/text\/html/i.test(contentType)) {
      const html = injectConsoleBootstrap(await response.text(), apiBase);
      res.writeHead(response.status, {
        "content-type": "text/html; charset=utf-8",
        // The Studio shell is upgraded with OpenViking; never cache it.
        "cache-control": "no-store",
      });
      res.end(html);
      return;
    }
    res.writeHead(response.status, {
      "content-type": contentType || "application/octet-stream",
      "cache-control": "no-cache",
    });
    if (req.method === "HEAD" || !response.body) {
      res.end();
      return;
    }
    try {
      for await (const chunk of response.body) res.write(chunk);
    } catch {
      // The frame may have navigated away mid-asset; nothing to repair.
    }
    res.end();
  };
}

/**
 * Probe both sides and return the JSON the browser half renders. A failure is
 * data, not an exception: the panel has an explicit state for every outcome.
 */
export async function collectConsoleInfo({
  config = {},
  client,
  fetchImpl,
  timeoutMs = CONSOLE_TIMEOUT_MS,
} = {}) {
  const serverUrl = normalizeConsoleUrl(config.baseUrl) || LOCAL_ENDPOINT;
  const consoleUrl = normalizeConsoleUrl(config.consoleUrl) || serverUrl;
  const browserUrl = resolveStudioUrl(consoleUrl) || resolveStudioUrl(serverUrl);
  return {
    ok: true,
    serverUrl,
    consoleUrl,
    browserUrl,
    health: await probeHealth(client),
    studio: await probeStudio({ browserUrl, fetchImpl, timeoutMs }),
  };
}

/**
 * Register the status route and the same-origin Studio proxy. Both need only
 * DSH's Web composition; terminal profiles skip them and keep working.
 */
export function mountConsole(ctx, config, client) {
  if (!ctx || typeof ctx.inject !== "function") return;
  ctx.inject(["webServer", "connection"], webCtx => {
    const fence = req => {
      try {
        return webCtx.connection.requestRejection(req) === undefined;
      } catch {
        return false;
      }
    };
    const handler = createConsoleRoute({ config, client, fence });
    webCtx.effect(
      () => webCtx.webServer.register({ kind: "exact", path: CONSOLE_ROUTE, handler }),
      "openvikingMemory.consoleRoute()",
    );
    const studioProxy = createStudioProxy({ config });
    webCtx.effect(
      () => webCtx.webServer.register({ kind: "prefix", path: STUDIO_PROXY_PATH, handler: studioProxy }),
      "openvikingMemory.studioProxy()",
    );
  });
}

/**
 * Build the status handler. `fence` is injected so the tests can exercise the
 * response contract without a live browser peer; `collect` is injectable for
 * the same reason.
 */
export function createConsoleRoute({
  config,
  client,
  fence,
  collect = collectConsoleInfo,
} = {}) {
  return async function consoleRoute(req, res) {
    let allowed = true;
    try {
      allowed = fence ? Boolean(fence(req)) : true;
    } catch {
      allowed = false;
    }
    if (!allowed) {
      writeJson(res, 403, { ok: false, error: { code: "forbidden", message: "forbidden" } });
      return;
    }
    if (req.method !== "GET") {
      writeJson(res, 405, { ok: false, error: { code: "method-not-allowed", message: "GET only" } });
      return;
    }
    try {
      const info = await collect({ config, client });
      writeJson(res, 200, info);
    } catch (error) {
      writeJson(res, 500, { ok: false, error: { code: "probe-failed", message: messageOf(error) } });
    }
  };
}

async function probeHealth(client) {
  const health = { reachable: false, healthy: false };
  try {
    const response = await client.healthResult();
    const result = response?.result || {};
    health.reachable = typeof response?.status === "number" ? response.status !== 0 : Boolean(response);
    health.healthy = Boolean(response?.ok) && result.healthy !== false;
    if (typeof result.version === "string") health.version = result.version;
    if (typeof result.auth_mode === "string") health.authMode = result.auth_mode;
    if (!response?.ok && response?.error?.message) health.error = String(response.error.message);
  } catch (error) {
    health.error = messageOf(error);
  }
  return health;
}

async function probeStudio({ browserUrl, fetchImpl, timeoutMs }) {
  const studio = { reachable: false, available: false };
  if (!browserUrl) return studio;
  let response;
  try {
    response = await (fetchImpl || globalThis.fetch)(browserUrl, {
      method: "GET",
      redirect: "follow",
      signal: AbortSignal.timeout(timeoutMs),
      headers: { accept: "text/html,application/xhtml+xml" },
    });
  } catch (error) {
    studio.error = messageOf(error);
    return studio;
  }
  studio.reachable = true;
  studio.status = response.status;
  const contentType = String(response.headers?.get?.("content-type") || "");
  const hasHtml = !contentType || /html/i.test(contentType);
  studio.available = response.status >= 200 && response.status < 300
    ? hasHtml
    : (response.status >= 300 && response.status < 400)
      || response.status === 401
      || response.status === 403;
  try {
    await response.body?.cancel?.();
  } catch {
    // Headers are all this probe needed.
  }
  return studio;
}

function messageOf(error) {
  return error instanceof Error ? error.message : String(error);
}

function writeJson(res, status, body) {
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  res.end(JSON.stringify(body));
}

function writeText(res, status, body) {
  res.writeHead(status, { "content-type": "text/plain; charset=utf-8" });
  res.end(body);
}
