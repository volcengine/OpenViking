import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
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

test("every hook.mjs event has a request budget inside the timeout its hooks.json gives it", () => {
  for (const [client, hostDir] of [["cursor", "cursor"], ["qoder", "qoder"], ["trae", "trae"], ["trae-cn", "trae"], ["zcode", "zcode"]]) {
    const { hooks } = JSON.parse(readFileSync(join(ROOT, "hosts", hostDir, "hooks.json"), "utf8"));
    const events = [];
    for (const entry of Object.values(hooks).flat()) {
      for (const leaf of entry.hooks || [entry]) {
        const event = /scripts\/hook\.mjs (\S+) /u.exec(leaf.command)?.[1];
        if (!event) continue;
        events.push(event);
        const budget = HOSTS[client].requestBudgets?.[event];
        // The seconds left over cover node start-up, the session lock and the state write.
        assert.ok(budget > 0 && budget <= leaf.timeout * 1000 - 3000,
          `${client} ${event}: request budget ${budget}ms, hook timeout ${leaf.timeout}s`);
      }
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

test("a capture keeps each request to the request timeout and queues what the stop budget cannot fit", async (t) => {
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
    fetchJSON: withRequestBudget(fetchJSON, 1500, cfg.timeoutMs),
    peerId: "",
    log: () => {},
  };
  const started = Date.now();
  await HOSTS.cursor.capture(ctx, {}, "preCompact");
  const elapsed = Date.now() - started;
  assert.ok(elapsed < 1500, `capture took ${elapsed}ms`);
  assert.deepEqual(server.requests, ["POST /api/v1/sessions/cu-budget/messages/batch"]);
  const queued = readdirSync(process.env.OPENVIKING_PENDING_DIR)
    .map((name) => JSON.parse(readFileSync(join(process.env.OPENVIKING_PENDING_DIR, name), "utf8")).type)
    .sort();
  assert.deepEqual(queued, ["addMessage", "addMessage", "commitSession"]);
});
