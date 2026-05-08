import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CommitQueue, type CommitClient } from "../capture/commit-queue.js";
import type { OVResult, OVTurn } from "../ov-client.js";
import { estimateTokens } from "../recall/rank.js";

interface MockClient extends CommitClient {
  appendTurns: ReturnType<typeof vi.fn>;
  commit: ReturnType<typeof vi.fn>;
}

function makeMockClient(overrides: {
  appendOk?: boolean;
  commitOk?: boolean;
  commitDelayMs?: number;
} = {}): MockClient {
  const appendOk = overrides.appendOk ?? true;
  const commitOk = overrides.commitOk ?? true;
  const commitDelay = overrides.commitDelayMs ?? 0;
  return {
    appendTurns: vi.fn(async (_sessionId: string, turns: OVTurn[]) => {
      const out: OVResult<{ written: number }> = appendOk
        ? { ok: true, value: { written: turns.length } }
        : { ok: false, error: { message: "append-fail" } };
      return out;
    }),
    commit: vi.fn(async () => {
      if (commitDelay) await new Promise((r) => setTimeout(r, commitDelay));
      const out: OVResult<unknown> = commitOk
        ? { ok: true, value: {} }
        : { ok: false, error: { message: "commit-fail" } };
      return out;
    }),
  };
}

const SESSION = "cp-test-session";
const SHORT_TURN: OVTurn = { role: "user", content: "x".repeat(40) }; // 10 tokens
const LONG_TURN: OVTurn = { role: "user", content: "x".repeat(400) }; // 100 tokens

describe("CommitQueue — append + token accumulation", () => {
  it("no-ops on empty turns array", async () => {
    const client = makeMockClient();
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 100, async: false });
    const res = await q.enqueue([]);
    expect(res).toEqual({ appended: 0, triggeredCommit: false, pendingAfter: 0 });
    expect(client.appendTurns).not.toHaveBeenCalled();
  });

  it("appends turns and accumulates tokens below threshold without committing", async () => {
    const client = makeMockClient();
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 100, async: false });

    const res = await q.enqueue([SHORT_TURN]);
    expect(res.appended).toBe(1);
    expect(res.triggeredCommit).toBe(false);
    expect(res.pendingAfter).toBe(estimateTokens(SHORT_TURN.content!));
    expect(q.pendingTokens).toBe(estimateTokens(SHORT_TURN.content!));
    expect(client.appendTurns).toHaveBeenCalledWith(SESSION, [SHORT_TURN]);
    expect(client.commit).not.toHaveBeenCalled();
  });

  it("accumulates tokens across multiple enqueues until the threshold is crossed", async () => {
    const client = makeMockClient();
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 25, async: false });

    await q.enqueue([SHORT_TURN]); // 10 tokens — under threshold
    expect(client.commit).not.toHaveBeenCalled();

    await q.enqueue([SHORT_TURN]); // 20 — still under
    expect(client.commit).not.toHaveBeenCalled();

    const res = await q.enqueue([SHORT_TURN]); // 30 — crosses
    expect(res.triggeredCommit).toBe(true);
    expect(client.commit).toHaveBeenCalledTimes(1);
    expect(q.pendingTokens).toBe(0); // reset on dispatch
  });

  it("dispatches commit when crossing the threshold in a single enqueue", async () => {
    const client = makeMockClient();
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 25, async: false });
    const res = await q.enqueue([LONG_TURN]); // 100 tokens, threshold 25
    expect(res.triggeredCommit).toBe(true);
    expect(client.commit).toHaveBeenCalledWith(SESSION, { force: false });
  });

  it("dispatches commit at *exactly* the threshold (>= comparison)", async () => {
    const client = makeMockClient();
    // turn content = 40 chars → 10 tokens. Threshold = 10 → exactly equal.
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 10, async: false });
    const res = await q.enqueue([SHORT_TURN]);
    expect(res.triggeredCommit).toBe(true);
  });
});

describe("CommitQueue — appendTurns failure", () => {
  it("does NOT accumulate tokens or trigger commit when appendTurns fails", async () => {
    const client = makeMockClient({ appendOk: false });
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 5, async: false });
    const res = await q.enqueue([LONG_TURN]); // would have crossed threshold

    expect(res.appended).toBe(0);
    expect(res.triggeredCommit).toBe(false);
    expect(q.pendingTokens).toBe(0);
    expect(client.commit).not.toHaveBeenCalled();
  });
});

describe("CommitQueue — flush", () => {
  it("forces a commit even with zero pending tokens", async () => {
    const client = makeMockClient();
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 1000, async: false });
    await q.flush();
    expect(client.commit).toHaveBeenCalledWith(SESSION, { force: true });
  });

  it("forces a commit when below threshold (session-close path)", async () => {
    const client = makeMockClient();
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 1000, async: false });
    await q.enqueue([SHORT_TURN]); // 10 tokens, well below 1000
    expect(client.commit).not.toHaveBeenCalled();

    await q.flush();
    expect(client.commit).toHaveBeenCalledWith(SESSION, { force: true });
    expect(q.pendingTokens).toBe(0);
  });
});

describe("CommitQueue — async vs sync dispatch", () => {
  it("with async=false, awaits the commit inline", async () => {
    const client = makeMockClient({ commitDelayMs: 30 });
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 5, async: false });
    const start = Date.now();
    await q.enqueue([SHORT_TURN]); // crosses threshold
    const elapsed = Date.now() - start;
    expect(elapsed).toBeGreaterThanOrEqual(25);
    expect(client.commit).toHaveBeenCalledTimes(1);
  });

  it("with async=true and an asyncSpawn factory, returns to caller without awaiting commit RTT", async () => {
    const client = makeMockClient({ commitDelayMs: 200 });
    const q = new CommitQueue({
      sessionId: SESSION,
      client,
      threshold: 5,
      async: true,
      // The factory just needs to produce DetachedSpawnOptions for an
      // existing executable. We point at /usr/bin/true (no-op) so the
      // spawn succeeds and runWriteTask reports detached:true. The
      // *real* commit work would happen inside the worker; the queue's
      // syncHandler is bypassed in the success path.
      asyncSpawn: () => ({ command: "/usr/bin/true", args: [] }),
    });

    const start = Date.now();
    await q.enqueue([SHORT_TURN]);
    const elapsed = Date.now() - start;
    // The queue's syncHandler (which would await the 200ms mock) must
    // NOT have been called; we should be back well under 200ms.
    expect(elapsed).toBeLessThan(150);
    expect(client.commit).not.toHaveBeenCalled();
  });

  it("with async=true but no asyncSpawn factory, falls back to inline (matches runWriteTask contract)", async () => {
    const client = makeMockClient();
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 5, async: true });
    await q.enqueue([SHORT_TURN]);
    expect(client.commit).toHaveBeenCalledWith(SESSION, { force: false });
  });
});

describe("CommitQueue — failure tolerance", () => {
  it("commit failure does not throw to the caller", async () => {
    const client = makeMockClient({ commitOk: false });
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 5, async: false });
    await expect(q.enqueue([SHORT_TURN])).resolves.not.toThrow();
  });

  it("commit failure resets the pending counter (data is on the server, next commit catches it)", async () => {
    const client = makeMockClient({ commitOk: false });
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 5, async: false });
    await q.enqueue([SHORT_TURN]);
    expect(q.pendingTokens).toBe(0);
  });
});

describe("CommitQueue — double-commit guard", () => {
  let originalSetTimeout: typeof setTimeout;
  beforeEach(() => { originalSetTimeout = globalThis.setTimeout; });
  afterEach(() => { globalThis.setTimeout = originalSetTimeout; });

  it("suppresses re-entrant flush() while a commit is in flight", async () => {
    let resolveCommit!: () => void;
    const client = makeMockClient();
    client.commit.mockImplementation(async () => {
      await new Promise<void>((r) => { resolveCommit = r; });
      return { ok: true, value: {} };
    });
    const q = new CommitQueue({ sessionId: SESSION, client, threshold: 1000, async: false });

    const first = q.flush();
    // The second flush, called while the first is still awaiting commit,
    // must be suppressed by the in-flight guard.
    const second = q.flush();
    resolveCommit!();
    await Promise.all([first, second]);

    expect(client.commit).toHaveBeenCalledTimes(1);
  });
});
