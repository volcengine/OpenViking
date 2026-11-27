import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { createSessionDrain } from "../takeover.ts";
import { enqueue, listPending } from "../shared/pending-queue.mjs";

const SESSION = "pi-session";

async function withPendingDir(fn) {
  const previous = process.env.OPENVIKING_PENDING_DIR;
  const dir = await mkdtemp(join(tmpdir(), "ov-pi-pending-"));
  process.env.OPENVIKING_PENDING_DIR = dir;
  try {
    return await fn();
  } finally {
    if (previous === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = previous;
    await rm(dir, { recursive: true, force: true });
  }
}

function batchFetch(respond = () => ({ ok: true, result: {} })) {
  const calls = [];
  const fetchJSON = async (path, init = {}) => {
    calls.push({ path, body: JSON.parse(init.body || "{}") });
    return respond();
  };
  return { fetchJSON, calls };
}

test("the takeover barrier drains this session's backlog in batches and leaves other sessions queued (#4504)", async () => {
  await withPendingDir(async () => {
    const { fetchJSON, calls } = batchFetch();
    const drain = createSessionDrain({ fetchJSON, sessionId: () => SESSION });
    const t0 = Date.now();
    for (let i = 0; i < 250; i++) await enqueue("addMessage", SESSION, { role: "user", content: `m${i}` }, { createdAt: t0 + i });
    await enqueue("addMessage", "other-session", { role: "user", content: "other" }, { createdAt: t0 + 999 });

    assert.equal(await drain.flush(), true);
    assert.deepEqual(calls.map((call) => call.body.messages.length), [100, 100, 50]);
    assert.equal(calls[2].body.messages[49].content, "m249");
    assert.deepEqual((await listPending()).map((p) => p.entry.sessionId), ["other-session"]);
    assert.equal(drain.dropped(), 0);
  });
});

test("a backlog entry the server refuses for good is dropped and counts toward the capture gap", async () => {
  await withPendingDir(async () => {
    const { fetchJSON } = batchFetch(() => ({ ok: false, status: 400, error: { message: "bad" } }));
    const drain = createSessionDrain({ fetchJSON, sessionId: () => SESSION });
    await enqueue("addMessage", SESSION, { role: "user", content: "poison" });

    await drain.flush();
    assert.equal(drain.dropped(), 1);
    assert.equal((await listPending()).length, 0);
  });
});
