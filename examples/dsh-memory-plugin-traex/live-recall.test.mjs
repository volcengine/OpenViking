import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import test from "node:test";
import { OpenVikingClient } from "./client.mjs";
import { resolveConfig } from "./config.mjs";
import { buildRecallBlock } from "./shared/recall-core.mjs";

// Real-backend recall gate: proves recall returns a genuine hit for content
// this test itself stored — the one property no stub or mock can certify.
// Opt-in because it needs a reachable OpenViking server with a working
// vector backend: set OPENVIKING_E2E=1 plus the usual credential chain
// (OPENVIKING_* env / ovcli.conf). Skipped otherwise, including in repo CI
// until a server secret is configured there.
const enabled = process.env.OPENVIKING_E2E === "1";

test("live recall returns a hit for a memory stored by this test", { skip: !enabled, timeout: 300_000 }, async () => {
  // keepRecentCount 0: with the default (10) a single-message session keeps
  // its whole tail verbatim and extraction has nothing to mine.
  const config = resolveConfig({ workspacePeer: false, scoreThreshold: 0.1, commitKeepRecentCount: 0 });
  const client = new OpenVikingClient(config);
  assert.equal(await client.health(), true, "OpenViking server must be reachable");

  const sentinel = `live-e2e-${randomUUID()}`;
  const sessionId = `dsh-live-e2e-${Date.now()}`;
  assert.equal(await client.ensureSession(sessionId), true);
  const added = await client.addMessage(sessionId, {
    role: "user",
    content: `Remember this fact: the deployment codename is ${sentinel}. It unlocks the staging cluster.`,
  });
  assert.equal(added.ok, true, "message append must succeed");
  const committed = await client.commitSession(sessionId);
  assert.equal(committed.ok, true, "commit must succeed");

  // Memory extraction is asynchronous server-side; wait for the queue, then
  // poll recall until the sentinel surfaces or the deadline passes.
  await client.fetchJSON("/api/v1/system/wait", {
    method: "POST",
    body: JSON.stringify({ timeout: 120 }),
  }, { timeoutMs: 125_000 });

  const deadline = Date.now() + 120_000;
  let block = null;
  while (Date.now() < deadline) {
    block = await buildRecallBlock(
      (path, init, options) => client.fetchJSON(path, init, options),
      config,
      `what is the deployment codename ${sentinel}`,
      {},
    );
    if (block !== null && block.includes(sentinel)) break;
    await new Promise(resolve => setTimeout(resolve, 5000));
  }
  assert.ok(block !== null && block.includes(sentinel),
    `recall block must contain the sentinel; got: ${block === null ? "null" : block.slice(0, 400)}`);
});
