/**
 * A fake OpenViking server, for tests that must observe what the plugin sends.
 *
 * It records every request — path, method, body, and the headers that identify
 * the caller — and lets a test script the answers per route. That is the only
 * way to assert the things that matter here: which messages reached the server
 * in which order, whether a commit followed them, and whether a retry happened
 * when it should not have.
 *
 * The port is chosen by the OS (`listen(0)`), so two tests can never collide.
 */

import http from "node:http";

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf8");
      try {
        resolve(raw ? JSON.parse(raw) : undefined);
      } catch {
        resolve(raw);
      }
    });
    req.on("error", reject);
  });
}

/**
 * Start a fake server.
 *
 * `handler(request, server)` returns a value, or a promise of one:
 *   { status, body }        an explicit answer
 *   undefined               the default: 200 `{status:"ok", result:{}}`
 *   "hang"                  never answer (for budget tests)
 */
export async function startFakeOv(handler = () => undefined) {
  const requests = [];
  const server = {
    requests,
    sessions: new Map(),
    /** Set to make every request fail with this status. */
    failWith: null,
    /** Set to make message posts fail with this status once. */
    failOnce: null,
  };

  const httpServer = http.createServer(async (req, res) => {
    const body = await readBody(req);
    const entry = {
      method: req.method,
      path: req.url,
      body,
      headers: { ...req.headers },
    };
    requests.push(entry);

    if (server.failWith) {
      res.writeHead(server.failWith, { "content-type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: { code: `http_${server.failWith}`, message: "scripted failure" } }));
      return;
    }
    if (server.failOnce && req.method === "POST" && server.failOnce.match.test(req.url)) {
      const status = server.failOnce.status;
      server.failOnce = null;
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify({ status: "error", error: { code: `http_${status}`, message: "scripted once" } }));
      return;
    }

    // Track what the server "holds" per session, so a test can assert on the
    // transcript it would archive rather than on the requests alone.
    const sessionMatch = /\/api\/v1\/sessions\/([^/?]+)/.exec(req.url || "");
    const sessionId = sessionMatch ? decodeURIComponent(sessionMatch[1]) : null;
    if (sessionId && req.method === "POST" && /\/messages\/batch$/.test(req.url)) {
      const list = server.sessions.get(sessionId) || [];
      list.push(...(body?.messages || []));
      server.sessions.set(sessionId, list);
    } else if (sessionId && req.method === "POST" && /\/messages$/.test(req.url)) {
      const list = server.sessions.get(sessionId) || [];
      list.push(body);
      server.sessions.set(sessionId, list);
    } else if (sessionId && req.method === "POST" && /\/commit$/.test(req.url)) {
      server.sessions.set(`${sessionId}::commits`, [
        ...(server.sessions.get(`${sessionId}::commits`) || []),
        body,
      ]);
    }

    const answer = await handler(entry, server);
    if (answer === "hang") return;
    const status = answer?.status ?? 200;
    const payload = answer?.body ?? { status: "ok", result: {} };
    res.writeHead(status, { "content-type": "application/json" });
    res.end(JSON.stringify(payload));
  });

  await new Promise((resolve) => httpServer.listen(0, "127.0.0.1", resolve));
  const { port } = httpServer.address();
  server.url = `http://127.0.0.1:${port}`;
  server.close = () => new Promise((resolve) => {
    // `close()` waits for live connections, and a handler that never answers
    // (the "hang" branch) would keep the runner alive forever.
    httpServer.closeAllConnections?.();
    httpServer.close(resolve);
  });
  server.reset = () => {
    requests.length = 0;
    server.sessions.clear();
  };
  server.messages = (sessionId) => server.sessions.get(sessionId) || [];
  server.commits = (sessionId) => server.sessions.get(`${sessionId}::commits`) || [];
  return server;
}

/** The `env` that points the plugin at a fake server. */
export function fakeOvEnv(server) {
  return {
    OPENVIKING_URL: server.url,
    OPENVIKING_API_KEY: "sk-test-key",
    OPENVIKING_AUTH_MODE: "api_key",
    OPENVIKING_CREDENTIAL_SOURCE: "env",
  };
}
