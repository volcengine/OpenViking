import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer as createHttpServer } from "node:http";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  loadAgentHookConfig,
  makeAgentFetchJSON,
  recallForPrompt,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { listPending } from "../../memory-plugin-shared/lib/pending-queue.mjs";
import { expectExit, runHookScript } from "../../memory-plugin-shared/testing/support.mjs";
import { HOSTS } from "../hosts/index.mjs";
import { withRequestBudget } from "../scripts/request-budget.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const dir = mkdtempSync(join(tmpdir(), "ov-request-budget-"));
process.on("exit", () => rmSync(dir, { recursive: true, force: true }));
process.env.OPENVIKING_STATE_DIR = join(dir, "state");
process.env.OPENVIKING_PENDING_DIR = join(dir, "pending");
process.env.OPENVIKING_HOME = join(dir, "home");

/** A server that takes every connection and never answers. */
async function silentServer(t) {
  const requests = [];
  const sockets = new Set();
  const server = createServer((socket) => {
    sockets.add(socket);
    socket.once("data", (chunk) => requests.push(String(chunk).split(" ").slice(0, 2).join(" ")));
    socket.on("error", () => {});
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => {
    for (const socket of sockets) socket.destroy();
    server.close();
  });
  return { port: server.address().port, requests };
}

function config(port, env = {}) {
  return loadAgentHookConfig("cursor", dir, {
    env: {
      OPENVIKING_URL: `http://127.0.0.1:${port}`,
      OPENVIKING_CLI_CONFIG_FILE: join(dir, "missing-ovcli.conf"),
      OPENVIKING_CONFIG_FILE: join(dir, "missing-ov.conf"),
      OPENVIKING_TIMEOUT_MS: "1000",
      ...env,
    },
  });
}

const MANIFESTS = {
  antigravity: "antigravity/hooks.json",
  codebuddy: "codebuddy/hooks.json",
  cursor: "cursor/hooks.json",
  kimicode: "kimicode/kimi.plugin.json",
  qoder: "qoder/hooks.json",
  trae: "trae/hooks.json",
  "trae-cn": "trae/hooks.json",
  zcode: "zcode/hooks.json",
};

/** Every hook command in a manifest, whatever the host nests it in. */
function commandHooks(value) {
  if (!value || typeof value !== "object") return [];
  if (typeof value.command === "string") return [value];
  return Object.values(value).flatMap(commandHooks);
}

test("every hook.mjs event has a request budget inside the timeout its manifest gives it", () => {
  assert.deepEqual(Object.keys(MANIFESTS).sort(), Object.keys(HOSTS).sort());
  for (const [client, manifest] of Object.entries(MANIFESTS)) {
    const events = [];
    for (const leaf of commandHooks(JSON.parse(readFileSync(join(ROOT, "hosts", manifest), "utf8")))) {
      const event = /scripts\/hook\.mjs"? (\S+) /u.exec(leaf.command)?.[1];
      if (!event) continue;
      events.push(event);
      const budget = HOSTS[client].requestBudgets?.[event];
      // The seconds left over cover node start-up, the session lock and the state write.
      assert.ok(budget > 0 && budget <= leaf.timeout * 1000 - 3000,
        `${client} ${event}: request budget ${budget}ms, hook timeout ${leaf.timeout}s`);
    }
    assert.ok(events.length >= 3, `${client}: found only ${events.join(", ")}`);
  }
});

test("recall stops falling back once the prompt budget is spent", async (t) => {
  const server = await silentServer(t);
  const cfg = config(server.port, { OPENVIKING_RECALL_CONTEXT_TIMEOUT_MS: "1000" });
  const { fetchJSON } = makeAgentFetchJSON(cfg, dir);
  const started = Date.now();
  // Unbounded, this chain is six rounds of one timeout each.
  const block = await recallForPrompt(withRequestBudget(fetchJSON, 2500, cfg.timeoutMs), cfg,
    "how do we deploy the service", dir, () => {}, { sessionId: "cu-budget" });
  const elapsed = Date.now() - started;
  assert.equal(block, null);
  assert.ok(elapsed < 2500, `recall took ${elapsed}ms`);
  assert.deepEqual(server.requests, ["POST /api/v1/search/search", "POST /api/v1/search/recall"]);
});

test("a capture timeout queues the batch and the commit behind it within the budget", async (t) => {
  const server = await silentServer(t);
  const cfg = config(server.port);
  const transcript = join(dir, "transcript.jsonl");
  writeFileSync(transcript, [
    JSON.stringify({ role: "user", message: { content: [{ type: "text", text: "question" }] } }),
    JSON.stringify({ role: "assistant", message: { content: [{ type: "text", text: "answer" }] } }),
  ].join("\n"));
  const { fetchJSON } = makeAgentFetchJSON(cfg, dir);
  const ctx = {
    cfg,
    input: { transcript_path: transcript },
    sessionId: "cu-budget",
    stateFile: join(dir, "budget.json"),
    fetchJSON: withRequestBudget(fetchJSON, 1500, cfg.timeoutMs),
    peerId: "",
    log: () => {},
  };
  const started = Date.now();
  const state = await HOSTS.cursor.capture(ctx, {}, "preCompact");
  const elapsed = Date.now() - started;
  assert.ok(elapsed < 1500, `capture took ${elapsed}ms`);
  assert.deepEqual(server.requests, ["POST /api/v1/sessions/cu-budget/messages/batch"]);
  assert.equal(state.capturedThrough, 1);
  assert.equal(state.capturedSinceCommit, 0);
  const queued = (await listPending()).map(({ entry }) => entry).sort((a, b) => a.createdAt - b.createdAt);
  assert.deepEqual(queued.map((entry) => entry.type), ["addMessage", "addMessage", "commitSession"]);
});

test("a capture request waits for captureTimeoutMs, not timeoutMs", async (t) => {
  const batches = [];
  const server = createHttpServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      if (request.url.endsWith("/messages/batch")) batches.push(JSON.parse(body).messages.length);
      setTimeout(() => response.end(JSON.stringify({ result: { ok: true } })), request.url.endsWith("/messages/batch") ? 1500 : 0);
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());
  const root = mkdtempSync(join(dir, "capture-timeout-"));
  const transcript = join(root, "transcript.jsonl");
  writeFileSync(transcript, [
    JSON.stringify({ role: "user", content: "question" }),
    JSON.stringify({ role: "assistant", content: "answer" }),
  ].join("\n"));
  const pendingDir = join(root, "pending");
  expectExit(await runHookScript(join(ROOT, "scripts", "hook.mjs"), {
    argv: ["stop", "cursor"],
    input: { conversation_id: "cursor-slow", cwd: root, transcript_path: transcript },
    env: {
      HOME: root,
      OPENVIKING_URL: `http://127.0.0.1:${server.address().port}`,
      OPENVIKING_CLI_CONFIG_FILE: join(root, "missing-ovcli.conf"),
      OPENVIKING_CONFIG_FILE: join(root, "missing-ov.conf"),
      OPENVIKING_HOOK_STATE_DIR: join(root, "state"),
      OPENVIKING_PENDING_DIR: pendingDir,
      OPENVIKING_MEMORY_ENABLED: "1",
      OPENVIKING_TIMEOUT_MS: "1000",
      OPENVIKING_CAPTURE_TIMEOUT_MS: "5000",
    },
  }));
  assert.deepEqual(batches, [2]);
  assert.equal(existsSync(pendingDir), false);
});
