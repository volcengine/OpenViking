import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { OpenVikingClient } from "./client.mjs";
import { resolveConfig } from "./config.mjs";
import { listPending } from "./shared/pending-queue.mjs";
import { deriveHarnessSessionId } from "./shared/session-model.mjs";
import { OpenVikingRuntime } from "./runtime.mjs";

// Runtime-to-real-server boundary gate (plugin runtime directly, not the DSH host):
// proves the property the scoped suite cannot — that a `compaction/start` (fed to the
// runtime the way the host's session/event subscription forwards it) makes the plugin
// issue its unconditional commit against a real
// OpenViking server, before the host rewrites the compacted range. The server's
// acceptance is asserted (task id + archive uri); the archive itself and the
// settlement count are server-side and polled as a closing log line. Mirrors the
// live-recall gate: opt-in via
// OPENVIKING_E2E=1 plus the usual credential chain (OPENVIKING_* env /
// ovcli.conf), skipped otherwise — including in repo CI until a server secret
// is configured there.
const enabled = process.env.OPENVIKING_E2E === "1";

test("live compaction boundary requests the commit against a real server", { skip: !enabled, timeout: 300_000 }, async () => {
  // keepRecentCount 0: with the default (10) the committed session keeps its
  // whole tail verbatim and extraction has nothing to mine — same rationale as
  // the live-recall gate.
  // Isolate the pending queue from other harnesses sharing ~/.openviking/pending.
  process.env.OPENVIKING_PENDING_DIR = mkdtempSync(join(tmpdir(), 'live-verify-pending-'));
  const config = resolveConfig({ workspacePeer: false, commitKeepRecentCount: 0 });
  const real = new OpenVikingClient(config);
  assert.equal((await real.healthResult()).ok, true, "OpenViking server must be reachable");

  const sentinel = `live-verify-${randomUUID()}`;
  const sessionId = `dsh-live-verify-${Date.now()}`;
  const ovSessionId = deriveHarnessSessionId("dsh-", sessionId);
  assert.equal((await real.ensureSessionResult(ovSessionId)).ok, true, "session creation must succeed");

  // Record what actually crosses the wire, so the assertions below read the
  // server's own numbers (pending_tokens, task_id) rather than trusting the
  // runtime's silence.
  const wire = [];
  const client = Object.create(real, {
    addMessage: { value: async (...a) => { const r = await real.addMessage(...a); wire.push(["addMessage", r]); return r; } },
    commitSession: { value: async (...a) => { const r = await real.commitSession(...a); wire.push(["commitSession", r]); return r; } },
  });

  // The runtime is fed the way the harness feeds it: a session object keyed by
  // id/cwd, capture() for message events, maybeCommit() for the durable
  // compaction bracket. No stub client — the real one, over the real wire.
  const runtime = new OpenVikingRuntime(client, config, { debug() {}, warn() {}, error() {} });
  const session = { id: sessionId, header: { cwd: mkdtempSync(join(tmpdir(), "live-verify-")) } };
  runtime.stateFor(session).ready = true;

  // Stage pending content: three user messages far below any commit threshold,
  // so the ONLY possible commit trigger below is the compaction boundary.
  for (let i = 1; i <= 3; i += 1) {
    runtime.capture(session, {
      type: "user/message",
      time: Date.now(),
      data: { content: [{ type: "text", text: `Boundary rig line ${i}: the deployment codename is ${sentinel}.` }] },
    });
  }
  await runtime.flush(session);
  const adds = wire.filter(([kind]) => kind === "addMessage").map(([, r]) => r);
  assert.equal(adds.length, 3, "every staged message must reach the server");
  for (const r of adds) assert.equal(r.ok, true, `addMessage must succeed (${JSON.stringify(r.error ?? {})})`);
  assert.ok(adds.at(-1).result.pending_tokens > 0, "messages must sit server-side as pending tokens before the boundary");

  // Nothing may have committed before the boundary: below the threshold, with no
  // turn/end and no teardown, the boundary is the only possible trigger.
  assert.equal(wire.filter(([kind]) => kind === "commitSession").length, 0,
    "no commit may fire before the compaction boundary");

  // The boundary. In the harness this event is appended by compaction-basic
  // before summarization; here it is fed directly, deterministically.
  runtime.maybeCommit(session, { type: "compaction/start" });
  await runtime.flush(session);

  const commit = wire.find(([kind]) => kind === "commitSession")?.[1];
  assert.ok(commit, "the boundary must issue a commitSession");
  assert.equal(commit.ok, true, `commitSession must succeed (${JSON.stringify(commit.error ?? {})})`);
  assert.equal(commit.result.status, "accepted", `commit must be accepted, got ${commit.result.status}`);
  assert.ok(commit.result.task_id, "accepted commit must carry a task id");
  assert.ok(commit.result.archive_uri?.includes(ovSessionId), "archive uri must point at this session");

  // The local retry queue must be empty: a boundary commit that needed a
  // replay would not be a boundary commit.
  assert.equal((await listPending()).length, 0, "boundary commit must leave no pending-queue residue");

  // Server-side settlement is asynchronous — poll the session stats until the
  // commit is visible (or a bounded timeout; the accepted task_id above is the
  // hard evidence, this readback is the closing confirmation).
  let settled = null;
  for (let i = 0; i < 10 && !settled; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 2_000));
    const s = await real.getSession(ovSessionId);
    if (s && (s.commit_count ?? 0) >= 1) settled = s;
  }

  console.log(`LIVE-VERIFY: PASS boundary=compaction/start pending_tokens=${adds.at(-1).result.pending_tokens} task=${commit.result.task_id} session=${ovSessionId} sentinel=${sentinel}`);
  console.log(`readback: ${settled ? `commit_count=${settled.commit_count} message_count=${settled.message_count}` : "settlement still pending at poll timeout (task accepted above)"}`);
});
