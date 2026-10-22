import assert from "node:assert/strict";
import test from "node:test";
import {
  buildStudioTarget,
  collectConsoleInfo,
  CONSOLE_ROUTE,
  createConsoleRoute,
  createStudioProxy,
  injectConsoleBootstrap,
  mountConsole,
  normalizeConsoleUrl,
  proxyTargetBase,
  resolveStudioUrl,
  STUDIO_PROXY_PATH,
} from "./console.mjs";

// The browser half's whole Host contract: URL derivation for every service
// shape, the same-origin Studio proxy that makes the iframe themeable, and the
// fenced read-only status route. No network is involved: the API client and
// fetch are stubs.

test("normalizeConsoleUrl keeps http(s) URLs and strips credentials and trailing slashes", () => {
  assert.equal(normalizeConsoleUrl("http://127.0.0.1:1933"), "http://127.0.0.1:1933/");
  assert.equal(normalizeConsoleUrl("http://127.0.0.1:1933/"), "http://127.0.0.1:1933/");
  assert.equal(normalizeConsoleUrl("http://127.0.0.1:1933/openviking/"), "http://127.0.0.1:1933/openviking");
  assert.equal(normalizeConsoleUrl("https://user:pass@ov.example.com/x"), "https://ov.example.com/x");
  assert.equal(normalizeConsoleUrl("ftp://ov.example.com"), undefined);
  assert.equal(normalizeConsoleUrl("not a url"), undefined);
  assert.equal(normalizeConsoleUrl(""), undefined);
  assert.equal(normalizeConsoleUrl(null), undefined);
});

test("resolveStudioUrl appends the Studio path to bases and preserves an explicit page URL", () => {
  assert.equal(resolveStudioUrl("http://127.0.0.1:1933"), "http://127.0.0.1:1933/studio/home");
  assert.equal(resolveStudioUrl("http://127.0.0.1:1933/"), "http://127.0.0.1:1933/studio/home");
  assert.equal(resolveStudioUrl("https://host/openviking"), "https://host/openviking/studio/home");
  assert.equal(resolveStudioUrl("https://host/openviking/studio"), "https://host/openviking/studio/home");
  assert.equal(resolveStudioUrl("https://host/studio/home"), "https://host/studio/home");
  assert.equal(
    resolveStudioUrl("https://console.example/studio/home?region=cn#x"),
    "https://console.example/studio/home?region=cn#x",
  );
  assert.equal(resolveStudioUrl("https://console.example/path"), "https://console.example/path/studio/home");
  assert.equal(resolveStudioUrl(""), undefined);
});

test("proxyTargetBase strips an explicit Studio suffix so the proxy path is appended once", () => {
  assert.equal(proxyTargetBase("http://127.0.0.1:1933"), "http://127.0.0.1:1933");
  assert.equal(proxyTargetBase("http://127.0.0.1:1933/"), "http://127.0.0.1:1933");
  assert.equal(proxyTargetBase("https://host/openviking"), "https://host/openviking");
  assert.equal(proxyTargetBase("https://host/openviking/studio"), "https://host/openviking");
  assert.equal(proxyTargetBase("https://host/openviking/studio/home"), "https://host/openviking");
  assert.equal(proxyTargetBase("ftp://host"), "http://127.0.0.1:1933");
  assert.equal(proxyTargetBase(""), "http://127.0.0.1:1933");
});

test("buildStudioTarget keeps the console path prefix and the request query", () => {
  assert.equal(
    buildStudioTarget({ baseUrl: "http://127.0.0.1:1933" }, "/studio/home"),
    "http://127.0.0.1:1933/studio/home",
  );
  assert.equal(
    buildStudioTarget({ baseUrl: "https://api.example.com", consoleUrl: "https://host/openviking" }, "/studio/assets/x.js", "?v=1"),
    "https://host/openviking/studio/assets/x.js?v=1",
  );
  assert.equal(
    buildStudioTarget({ baseUrl: "http://127.0.0.1:1933" }, "/home"),
    "http://127.0.0.1:1933/studio/home",
  );
});

test("injectConsoleBootstrap points the Studio at the API and leaves the key out", () => {
  const html = injectConsoleBootstrap(
    "<!doctype html><html><head><title>OpenViking Studio</title></head><body></body></html>",
    "http://127.0.0.1:1933/",
  );
  assert.match(html, /<head><script>/);
  assert.match(html, /ov_console_connection_profiles/);
  assert.match(html, /baseUrl/);
  assert.match(html, /127\.0\.0\.1:1933/);
  assert.match(html, /"theme", "system"/);
  // The profile always carries the key field, but never a configured value.
  assert.equal(html.includes("secret"), false);

  const bare = injectConsoleBootstrap("<html><body>x</body></html>", "http://127.0.0.1:1933/");
  assert.ok(bare.startsWith("<script>"));
});

test("collectConsoleInfo probes health and the direct Studio page without the key", async () => {
  let seen;
  const info = await collectConsoleInfo({
    config: {
      baseUrl: "http://127.0.0.1:1933",
      apiKey: "secret",
    },
    client: healthyClient(),
    fetchImpl: async (url, init) => {
      seen = { url, init };
      return htmlResponse();
    },
  });

  assert.equal(info.serverUrl, "http://127.0.0.1:1933/");
  assert.equal(info.consoleUrl, "http://127.0.0.1:1933/");
  assert.equal(info.browserUrl, "http://127.0.0.1:1933/studio/home");
  assert.deepEqual(info.health, { reachable: true, healthy: true, version: "0.5.1.1", authMode: "dev" });
  assert.deepEqual(info.studio, { reachable: true, available: true, status: 200 });
  assert.equal(seen.url, info.browserUrl);
  assert.equal(seen.init.headers.authorization, undefined);
  assert.equal(JSON.stringify(info).includes("secret"), false);
});

test("collectConsoleInfo follows an explicit console URL for a separate console host", async () => {
  const info = await collectConsoleInfo({
    config: {
      baseUrl: "https://api.example.com/openviking",
      apiKey: "secret",
      consoleUrl: "https://console.example.com/openviking",
    },
    client: healthyClient(),
    fetchImpl: async () => htmlResponse(401),
  });

  assert.equal(info.serverUrl, "https://api.example.com/openviking");
  assert.equal(info.consoleUrl, "https://console.example.com/openviking");
  assert.equal(info.browserUrl, "https://console.example.com/openviking/studio/home");
  assert.equal(info.studio.available, true);
  assert.equal(info.studio.status, 401);
  assert.equal(JSON.stringify(info).includes("secret"), false);
});

test("collectConsoleInfo reports every failure as data", async () => {
  const down = await collectConsoleInfo({
    config: { baseUrl: "http://127.0.0.1:1933" },
    client: failingClient(),
    fetchImpl: async () => {
      throw new Error("connect ECONNREFUSED");
    },
  });
  assert.equal(down.health.reachable, false);
  assert.equal(down.health.error, "fetch failed");
  assert.equal(down.studio.reachable, false);
  assert.equal(down.studio.error, "connect ECONNREFUSED");

  const missing = await collectConsoleInfo({
    config: { baseUrl: "http://127.0.0.1:1933" },
    client: healthyClient(),
    fetchImpl: async () => new Response("not found", { status: 404, headers: { "content-type": "text/html" } }),
  });
  assert.equal(missing.studio.reachable, true);
  assert.equal(missing.studio.available, false);
});

test("createStudioProxy injects the bootstrap into HTML and streams assets", async () => {
  let seen;
  const proxy = createStudioProxy({
    config: { baseUrl: "http://127.0.0.1:1933", apiKey: "secret" },
    fetchImpl: async url => {
      seen = url;
      return htmlResponse();
    },
  });
  const html = await callProxy(proxy, "/studio/home");
  assert.equal(html.status, 200);
  assert.equal(html.headers["cache-control"], "no-store");
  assert.match(html.body, /ov_console_connection_profiles/);
  assert.equal(seen, "http://127.0.0.1:1933/studio/home");
  assert.equal(html.body.includes("secret"), false);

  const assetProxy = createStudioProxy({
    config: { baseUrl: "http://127.0.0.1:1933" },
    fetchImpl: async () => ({
      status: 200,
      headers: new Headers({ "content-type": "text/javascript" }),
      body: (async function* () {
        yield Buffer.from("console.log(1)");
      })(),
    }),
  });
  const asset = await callProxy(assetProxy, "/studio/assets/app.js");
  assert.equal(asset.status, 200);
  assert.equal(asset.headers["content-type"], "text/javascript");
  assert.equal(asset.body, "console.log(1)");

  const post = await callProxy(proxy, "/studio/home", { method: "POST" });
  assert.equal(post.status, 405);

  const failed = await callProxy(createStudioProxy({
    config: { baseUrl: "http://127.0.0.1:1933" },
    fetchImpl: async () => {
      throw new Error("down");
    },
  }), "/studio/home");
  assert.equal(failed.status, 502);
});

test("createConsoleRoute fences, rejects non-GET, and returns the no-store JSON", async () => {
  const config = { baseUrl: "http://127.0.0.1:1933" };
  const client = healthyClient();
  const collect = async ({ config: seenConfig }) => ({ ok: true, serverUrl: seenConfig.baseUrl });

  const forbidden = await callRoute(createConsoleRoute({ config, client, collect, fence: () => false }));
  assert.equal(forbidden.status, 403);
  assert.equal(forbidden.headers["cache-control"], "no-store");

  const post = await callRoute(createConsoleRoute({ config, client, collect, fence: () => true }), { method: "POST" });
  assert.equal(post.status, 405);

  const ok = await callRoute(createConsoleRoute({ config, client, collect, fence: () => true }));
  assert.equal(ok.status, 200);
  assert.deepEqual(JSON.parse(ok.body), { ok: true, serverUrl: "http://127.0.0.1:1933" });
  assert.equal(ok.headers["content-type"], "application/json; charset=utf-8");
  assert.equal(ok.headers["cache-control"], "no-store");

  const failed = await callRoute(createConsoleRoute({
    config,
    client,
    fence: () => true,
    collect: async () => {
      throw new Error("probe failed");
    },
  }));
  assert.equal(failed.status, 500);
  assert.equal(JSON.parse(failed.body).ok, false);
});

test("mountConsole registers the fenced status route and the same-origin Studio proxy", () => {
  const registered = [];
  const webCtx = {
    connection: { requestRejection: () => undefined },
    webServer: {
      register(route) {
        registered.push(route);
        return () => {};
      },
    },
    effect(execute) {
      execute();
      return () => {};
    },
  };
  const ctx = {
    inject(deps, callback) {
      assert.deepEqual(deps, ["webServer", "connection"]);
      callback(webCtx);
    },
  };

  mountConsole(ctx, { baseUrl: "http://127.0.0.1:1933" }, healthyClient());
  assert.deepEqual(registered.map(route => [route.kind, route.path]), [
    ["exact", CONSOLE_ROUTE],
    ["prefix", STUDIO_PROXY_PATH],
  ]);
  assert.equal(registered.every(route => typeof route.handler === "function"), true);

  assert.doesNotThrow(() => mountConsole({ effect() {} }, {}, {}));
  assert.doesNotThrow(() => mountConsole(undefined, {}, {}));
});

function healthyClient() {
  return {
    healthResult: async () => ({
      ok: true,
      status: 200,
      result: { healthy: true, version: "0.5.1.1", auth_mode: "dev" },
    }),
  };
}

function failingClient() {
  return {
    healthResult: async () => ({
      ok: false,
      status: 0,
      result: null,
      error: { message: "fetch failed" },
    }),
  };
}

function htmlResponse(status = 200, headers = {}) {
  return new Response("<!doctype html><title>OpenViking Studio</title>", {
    status,
    headers: { "content-type": "text/html; charset=utf-8", ...headers },
  });
}

async function callProxy(handler, url, overrides = {}) {
  const out = { status: 0, headers: {}, body: "" };
  const res = {
    writeHead(status, headers) {
      out.status = status;
      out.headers = headers || {};
    },
    write(chunk) {
      out.body += Buffer.isBuffer(chunk) ? chunk.toString("utf8") : String(chunk);
    },
    end(body) {
      if (body) out.body += String(body);
    },
  };
  await handler({
    method: "GET",
    url,
    headers: { accept: "*/*" },
    ...overrides,
  }, res);
  return out;
}

async function callRoute(handler, overrides = {}) {
  const out = { status: 0, headers: {}, body: "" };
  const res = {
    writeHead(status, headers) {
      out.status = status;
      out.headers = headers || {};
    },
    end(body) {
      out.body = body || "";
    },
  };
  const req = {
    method: "GET",
    headers: { referer: "http://127.0.0.1:50422/" },
    ...overrides,
  };
  await handler(req, res);
  return out;
}
