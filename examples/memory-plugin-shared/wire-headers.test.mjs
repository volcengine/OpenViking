import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { buildPluginConfig } from "./lib/plugin-config.mjs";
import { buildProxyConnection } from "./lib/credentials.mjs";
import { createOvHttp } from "./lib/ov-http.mjs";
import { withMockOpenViking, writeJson } from "./testing/support.mjs";

import { makeAgentFetchJSON } from "./lib/agent-hook-runtime.mjs";
import { makeFetchJSON as ccMakeFetchJSON } from "../claude-code-memory-plugin/scripts/lib/ov-session.mjs";
import { makeFetchJSON as codexMakeFetchJSON } from "../codex-memory-plugin/scripts/ov-session.mjs";
import { OpenVikingClient } from "../dsh-memory-plugin/client.mjs";
import { fetchJSON as opencodeFetchJSON } from "../opencode-plugin/lib/utils.mjs";
import { OVClient } from "../pi-coding-agent-extension/client.ts";

/**
 * Every harness talks to the same server, so every harness has to put the same
 * thing on the wire. They did not: Codex sent the api key twice (`Authorization`
 * and `X-API-Key`), Claude Code read `accountId` where the rest read `account`,
 * and only Codex asked whether the server was in trusted mode before naming the
 * operator to every proxy on the path.
 *
 * These assertions pin the one shape. They are also what makes the later merge
 * into a single HTTP module provable as a no-op on the wire: if the merged
 * builder drifts, six harnesses fail here at once.
 */

const BASE_URL = "http://127.0.0.1:1933";

const CFG = {
  apiKey: "secret",
  account: "acct-a",
  user: "user-a",
  peerId: "peer-a",
  userAgent: "openviking-memory-test/9.9.9",
  timeoutMs: 2000,
  captureTimeoutMs: 2000,
  requestTimeoutMs: 2000,
  baseUrl: BASE_URL,
  endpoint: BASE_URL,
};

const TRUSTED = {
  "Content-Type": "application/json",
  "Authorization": "Bearer secret",
  "X-OpenViking-Account": "acct-a",
  "X-OpenViking-User": "user-a",
  "X-OpenViking-Actor-Peer": "peer-a",
  "User-Agent": "openviking-memory-test/9.9.9",
};

const API_KEY_ONLY = {
  "Content-Type": "application/json",
  "Authorization": "Bearer secret",
  "X-OpenViking-Actor-Peer": "peer-a",
  "User-Agent": "openviking-memory-test/9.9.9",
};

const STACKS = [
  {
    name: "claude-code session helper",
    send: (cfg) => ccMakeFetchJSON(cfg)("/health", {}, { actorPeerId: cfg.peerId }),
  },
  {
    name: "codex session helper",
    send: (cfg) => codexMakeFetchJSON(cfg, { getActorPeerId: () => cfg.peerId }).fetchJSONRes("/health"),
  },
  {
    name: "thin agent hook runtime",
    send: (cfg) => makeAgentFetchJSON(cfg, process.cwd()).fetchJSON("/health"),
  },
  {
    name: "opencode client",
    send: (cfg) => opencodeFetchJSON(cfg, "/health", {}, { actorPeerId: cfg.peerId }),
  },
  {
    name: "dsh client",
    send: (cfg) => new OpenVikingClient(cfg).fetchJSON("/health"),
  },
  {
    name: "pi client",
    send: (cfg) => new OVClient(cfg).fetchJSON("/health"),
  },
];

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

async function captureHeaders(stack, cfg) {
  let seen = null;
  globalThis.fetch = async (_url, init) => {
    seen = init.headers;
    return new Response(JSON.stringify({ status: "ok", result: {} }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };
  await stack.send(cfg);
  assert.ok(seen, `${stack.name} made no request`);
  return { ...seen };
}

for (const stack of STACKS) {
  test(`${stack.name} sends the same headers as every other stack`, async () => {
    assert.deepEqual(
      await captureHeaders(stack, { ...CFG, authMode: "trusted", sendIdentityHeaders: true }),
      TRUSTED,
    );
    assert.deepEqual(
      await captureHeaders(stack, { ...CFG, authMode: "api_key", sendIdentityHeaders: false }),
      API_KEY_ONLY,
    );
  });
}

test("auth mode environment overrides file modes on the wire", async () => {
  const dir = await mkdtemp(join(tmpdir(), "ov-auth-mode-wire-"));
  try {
    const cli = join(dir, "ovcli.conf");
    const ov = join(dir, "ov.conf");
    await withMockOpenViking((_req, res) => writeJson(res, { status: "ok", result: {} }), async (baseUrl, requests) => {
      for (const [mode, fileMode] of [["api_key", "trusted"], ["trusted", "api_key"]]) {
        await writeFile(cli, JSON.stringify({ url: baseUrl, api_key: "test-key", account: "acct", user: "usr", plugin: { authMode: fileMode } }));
        await writeFile(ov, JSON.stringify({ server: { auth_mode: fileMode }, codex: { authMode: fileMode } }));
        const env = {
          OPENVIKING_HOME: dir, OPENVIKING_CLI_CONFIG_FILE: cli, OPENVIKING_CONFIG_FILE: ov, OPENVIKING_AUTH_MODE: mode,
        };
        for (const cfg of [buildPluginConfig("codex", { cwd: dir, env }), buildProxyConnection("agent-plugins", { env })]) {
          await createOvHttp(cfg)("/health");
          const headers = requests.at(-1).headers;
          assert.equal(headers.authorization, "Bearer test-key");
          assert.equal(headers["x-openviking-account"], mode === "trusted" ? "acct" : undefined);
          assert.equal(headers["x-openviking-user"], mode === "trusted" ? "usr" : undefined);
        }
      }
    });
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
