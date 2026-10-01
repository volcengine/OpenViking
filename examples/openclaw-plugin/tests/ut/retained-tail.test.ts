import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient, OVMessage } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { createMemoryOpenVikingContextEngine } from "../../context-engine.js";
import {
  deleteRetainedTail,
  getRetainedTail,
  mergeTail,
  setRetainedTail,
} from "../../services/retained-tail.js";

const ids = (messages: Array<{ id: string }>) => messages.map((m) => m.id);
const ref = (id: string) => ({ id });

function msg(id: string, role: "user" | "assistant" = "user"): OVMessage {
  return { id, role, parts: [{ type: "text", text: id }], created_at: "2026-09-29T00:00:00Z" };
}

// Alternating user/assistant messages keep the assembled output free of
// merges, so the output can be compared message by message.
function conversation(prefix: string, count: number): OVMessage[] {
  return Array.from({ length: count }, (_, i) =>
    msg(`${prefix}${i + 1}`, i % 2 === 0 ? "user" : "assistant"));
}

function context(messages: OVMessage[], overview = "") {
  return { latest_archive_overview: overview, pre_archive_abstracts: [], messages, estimatedTokens: 0 };
}

function texts(messages: Array<{ content: unknown }>): string[] {
  return messages.map(({ content }) => typeof content === "string"
    ? content
    : (content as Array<{ type: string; text?: string }>)
      .filter((block) => block.type === "text")
      .map((block) => block.text)
      .join(""));
}

let sessionCounter = 0;
const uniqueSession = () => `retained-tail-${++sessionCounter}`;

function makeClient() {
  return {
    addSessionMessage: vi.fn().mockResolvedValue(undefined),
    getSession: vi.fn().mockResolvedValue({ pending_tokens: 100_000 }),
    getSessionContext: vi.fn(),
    commitSession: vi.fn().mockResolvedValue({ status: "accepted", archived: true, task_id: "task-1" }),
  };
}

function makeEngine(
  client: ReturnType<typeof makeClient>,
  opts: { cfg?: Record<string, unknown>; hostVersion?: string } = {},
) {
  const logger = { info: vi.fn(), warn: vi.fn(), error: vi.fn() };
  const engine = createMemoryOpenVikingContextEngine({
    id: "openviking",
    name: "Context Engine (OpenViking)",
    version: "test",
    hostVersion: opts.hostVersion,
    cfg: memoryOpenVikingConfigSchema.parse({
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: true,
      autoRecall: false,
      commitKeepRecentCount: 3,
      emitStandardDiagnostics: true,
      ...opts.cfg,
    }),
    logger,
    getClient: vi.fn().mockResolvedValue(client) as unknown as () => Promise<OpenVikingClient>,
    resolveAgentId: () => "agent",
  });
  return { engine, logger };
}

function lastDiag(logger: ReturnType<typeof makeEngine>["logger"], stage: string): Record<string, unknown> {
  const entries = logger.info.mock.calls
    .map(([line]) => String(line))
    .filter((line) => line.startsWith("openviking: diag "))
    .map((line) => JSON.parse(line.slice("openviking: diag ".length)))
    .filter((entry) => entry.stage === stage);
  return entries.at(-1)?.data;
}

const turn = [
  { role: "user", content: "a new question for the session" },
  { role: "assistant", content: "an answer to that question" },
];

function afterTurnParams(sessionId: string) {
  return { sessionId, sessionFile: "", messages: turn, prePromptMessageCount: 0 };
}

describe("mergeTail", () => {
  it("prepends the whole tail when nothing overlaps", () => {
    expect(ids(mergeTail([ref("a"), ref("b")], [ref("c"), ref("d")]))).toEqual(["a", "b", "c", "d"]);
  });

  it("adds nothing when the messages already contain the whole tail", () => {
    expect(ids(mergeTail([ref("b"), ref("c")], [ref("a"), ref("b"), ref("c"), ref("d")])))
      .toEqual(["a", "b", "c", "d"]);
  });

  it("prepends only the part older than the first overlapping message", () => {
    expect(ids(mergeTail([ref("a"), ref("b"), ref("c")], [ref("b"), ref("c"), ref("d")])))
      .toEqual(["a", "b", "c", "d"]);
  });

  it("does not move messages the server dropped from the middle ahead of kept ones", () => {
    const tail = ["q1", "c1", "a1", "q2", "c2", "c3", "a2"].map(ref);
    expect(ids(mergeTail(tail, [ref("q2"), ref("a2")]))).toEqual(["q1", "c1", "a1", "q2", "a2"]);
  });

  it("returns the messages for an empty tail and the tail for empty messages", () => {
    expect(ids(mergeTail([], [ref("a"), ref("b")]))).toEqual(["a", "b"]);
    expect(ids(mergeTail([ref("a"), ref("b")], []))).toEqual(["a", "b"]);
  });
});

describe("retained tail store", () => {
  it("evicts the least recently stored session beyond 1024 sessions", () => {
    const sessions = Array.from({ length: 1025 }, () => uniqueSession());
    const [first, second] = sessions;
    for (const session of sessions.slice(0, -1)) setRetainedTail(session, [msg("m")]);
    setRetainedTail(first, [msg("m")]);
    setRetainedTail(sessions.at(-1)!, [msg("m")]);

    expect(getRetainedTail(first)).toBeDefined();
    expect(getRetainedTail(second)).toBeUndefined();
    expect(getRetainedTail(sessions.at(-1)!)).toBeDefined();
    for (const session of sessions) deleteRetainedTail(session);
  });
});

describe("context engine retained tail", () => {
  it("captures the tail before an afterTurn commit that archives everything", async () => {
    const session = uniqueSession();
    const client = makeClient();
    client.getSessionContext.mockResolvedValue(context(conversation("m", 6)));
    const { engine, logger } = makeEngine(client);

    await engine.afterTurn!(afterTurnParams(session));

    expect(client.getSessionContext).toHaveBeenCalledWith(session, 128_000);
    expect(client.getSessionContext.mock.invocationCallOrder[0])
      .toBeLessThan(client.commitSession.mock.invocationCallOrder[0]);
    expect(client.commitSession).toHaveBeenCalledWith(session, { wait: false, keepRecentCount: 0 });
    expect(ids(getRetainedTail(session)!)).toEqual(["m4", "m5", "m6"]);
    expect(lastDiag(logger, "afterTurn_commit")).toMatchObject({ archived: true, retainedTailMessages: 3 });
  });

  it("assembles overview + tail + live messages once the archive has completed", async () => {
    const session = uniqueSession();
    const client = makeClient();
    client.getSessionContext.mockResolvedValueOnce(context(conversation("m", 6)));
    const { engine, logger } = makeEngine(client);
    await engine.afterTurn!(afterTurnParams(session));

    client.getSessionContext.mockResolvedValueOnce(context([msg("n1"), msg("n2", "assistant")], "SUMMARY"));
    const result = await engine.assemble({ sessionId: session, messages: [], prompt: "next question" });

    expect(texts(result.messages)).toEqual([
      "[Session History Summary]\nSUMMARY",
      "m4",
      "m5",
      "m6",
      "n1",
      "n2",
    ]);
    expect(lastDiag(logger, "assemble_result")).toMatchObject({ activeCount: 5, retainedTailMessages: 3 });
  });

  it("assembles without duplicates or reordering while the archive is pending", async () => {
    const session = uniqueSession();
    const client = makeClient();
    client.getSessionContext.mockResolvedValueOnce(context(conversation("m", 6)));
    const { engine } = makeEngine(client);
    await engine.afterTurn!(afterTurnParams(session));

    // The server budget kept only part of the pending archive's raw messages.
    const pending = [msg("m5"), msg("m6", "assistant"), msg("n1"), msg("n2", "assistant")];
    client.getSessionContext.mockResolvedValueOnce(context(pending, "OLDER SUMMARY"));
    const result = await engine.assemble({ sessionId: session, messages: [], prompt: "next question" });

    expect(texts(result.messages)).toEqual([
      "[Session History Summary]\nOLDER SUMMARY",
      "m4",
      "m5",
      "m6",
      "n1",
      "n2",
    ]);
  });

  it("counts the tail when deciding whether to fall back to host messages without an overview", async () => {
    const session = uniqueSession();
    setRetainedTail(session, [msg("t1"), msg("t2", "assistant")]);
    const client = makeClient();
    const { engine, logger } = makeEngine(client);
    const host = [
      { role: "user", content: "h1" },
      { role: "assistant", content: "h2" },
      { role: "user", content: "h3" },
    ];

    client.getSessionContext.mockResolvedValueOnce(context([]));
    const tailOnly = await engine.assemble({ sessionId: session, messages: [], prompt: "next" });
    expect(texts(tailOnly.messages)).toEqual(["t1", "t2"]);

    client.getSessionContext.mockResolvedValueOnce(context([msg("n1"), msg("n2", "assistant")]));
    const merged = await engine.assemble({ sessionId: session, messages: host, prompt: "next" });
    expect(texts(merged.messages)).toEqual(["t1", "t2", "n1", "n2"]);

    client.getSessionContext.mockResolvedValueOnce(context([msg("n1")]));
    const fallback = await engine.assemble({ sessionId: session, messages: [...host, ...host], prompt: "next" });
    expect(fallback.messages).toHaveLength(6);
    expect(lastDiag(logger, "assemble_result")).toMatchObject({
      reason: "ov_msgs_fewer_than_input",
      activeCount: 3,
      retainedTailMessages: 2,
    });
    deleteRetainedTail(session);
  });

  it("keeps the tail length across consecutive commits with few live messages", async () => {
    const session = uniqueSession();
    const client = makeClient();
    const { engine } = makeEngine(client, { cfg: { commitKeepRecentCount: 4 } });

    client.getSessionContext.mockResolvedValueOnce(context(conversation("m", 6)));
    await engine.afterTurn!(afterTurnParams(session));
    client.getSessionContext.mockResolvedValueOnce(context([msg("n1"), msg("n2", "assistant")], "SUMMARY"));
    await engine.afterTurn!(afterTurnParams(session));

    expect(ids(getRetainedTail(session)!)).toEqual(["m5", "m6", "n1", "n2"]);
  });

  it("shares the tail between engine instances", async () => {
    const session = uniqueSession();
    const client = makeClient();
    client.getSessionContext.mockResolvedValueOnce(context(conversation("m", 6)));
    await makeEngine(client).engine.afterTurn!(afterTurnParams(session));

    client.getSessionContext.mockResolvedValueOnce(context([], "SUMMARY"));
    const result = await makeEngine(client).engine.assemble({ sessionId: session, messages: [], prompt: "next" });

    expect(texts(result.messages)).toEqual(["[Session History Summary]\nSUMMARY", "m4", "m5", "m6"]);
  });

  it("keeps no tail and skips the capture read when commitKeepRecentCount is 0", async () => {
    const session = uniqueSession();
    const client = makeClient();
    const { engine } = makeEngine(client, { cfg: { commitKeepRecentCount: 0 } });

    await engine.afterTurn!(afterTurnParams(session));

    expect(client.getSessionContext).not.toHaveBeenCalled();
    expect(client.commitSession).toHaveBeenCalledWith(session, { wait: false, keepRecentCount: 0 });
    expect(getRetainedTail(session)).toBeUndefined();
  });

  it("keeps server-side retention and no tail in turn_budget mode", async () => {
    const session = uniqueSession();
    const client = makeClient();
    const { engine } = makeEngine(client, { cfg: { commitRetentionMode: "turn_budget" } });

    await engine.afterTurn!(afterTurnParams(session));

    expect(client.getSessionContext).not.toHaveBeenCalled();
    expect(client.commitSession).toHaveBeenCalledWith(session, { wait: false, retentionMode: "turn_budget" });
    expect(getRetainedTail(session)).toBeUndefined();
  });

  it("stores no tail when the commit archived nothing", async () => {
    const session = uniqueSession();
    const client = makeClient();
    client.getSessionContext.mockResolvedValue(context(conversation("m", 6)));
    client.commitSession.mockResolvedValue({ status: "skipped", archived: false });
    const { engine } = makeEngine(client);

    await engine.afterTurn!(afterTurnParams(session));

    expect(client.commitSession).toHaveBeenCalledTimes(1);
    expect(getRetainedTail(session)).toBeUndefined();
  });

  it("still commits and drops the old tail when the capture read fails", async () => {
    const session = uniqueSession();
    setRetainedTail(session, [msg("stale")]);
    const client = makeClient();
    client.getSessionContext.mockRejectedValue(new Error("context unavailable"));
    const { engine, logger } = makeEngine(client);

    await engine.afterTurn!(afterTurnParams(session));

    expect(client.commitSession).toHaveBeenCalledWith(session, { wait: false, keepRecentCount: 0 });
    expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining("tail capture failed"));
    expect(getRetainedTail(session)).toBeUndefined();
  });

  it("still commits on the durable path when the capture read fails", async () => {
    const session = uniqueSession();
    const client = makeClient();
    client.getSessionContext.mockRejectedValue(new Error("context unavailable"));
    const { engine } = makeEngine(client, { hostVersion: "2026.9.3" });

    await expect(engine.commitTurn({ advancementKey: "k1", sessionId: session, messages: turn }))
      .resolves.toEqual({ status: "committed" });

    expect(client.commitSession).toHaveBeenCalledWith(session, { wait: false, keepRecentCount: 0 });
    expect(getRetainedTail(session)).toBeUndefined();
  });

  it("drops the old tail when the commit throws after the server may have archived", async () => {
    const session = uniqueSession();
    setRetainedTail(session, [msg("stale")]);
    const client = makeClient();
    client.getSessionContext.mockResolvedValue(context(conversation("m", 6)));
    client.commitSession.mockRejectedValue(new Error("request timed out"));
    const { engine } = makeEngine(client);

    await engine.afterTurn!(afterTurnParams(session));

    expect(getRetainedTail(session)).toBeUndefined();
  });

  it("drops the old tail and rethrows when the durable commit throws", async () => {
    const session = uniqueSession();
    setRetainedTail(session, [msg("stale")]);
    const client = makeClient();
    client.getSessionContext.mockResolvedValue(context(conversation("m", 6)));
    client.commitSession.mockRejectedValue(new Error("request timed out"));
    const { engine } = makeEngine(client, { hostVersion: "2026.9.3" });

    await expect(engine.commitTurn({ advancementKey: "k1", sessionId: session, messages: turn }))
      .rejects.toThrow("request timed out");

    expect(getRetainedTail(session)).toBeUndefined();
  });

  it("compacts right after an auto-commit by dropping the tail", async () => {
    const session = uniqueSession();
    const client = makeClient();
    client.getSessionContext.mockResolvedValueOnce(context(conversation("m", 6)));
    const { engine } = makeEngine(client);
    await engine.afterTurn!(afterTurnParams(session));
    expect(getRetainedTail(session)).toHaveLength(3);

    client.getSessionContext.mockResolvedValue(context([], "SUMMARY"));
    client.commitSession.mockResolvedValue({
      session_id: session,
      status: "skipped",
      task_id: null,
      archive_uri: null,
      archived: false,
      reason: "no_messages",
    });
    const result = await engine.compact({ sessionId: session, sessionFile: "", tokenBudget: 4096, force: true });

    expect(result).toMatchObject({ ok: true, compacted: true, result: { summary: "SUMMARY", firstKeptEntryId: "" } });
    expect(getRetainedTail(session)).toBeUndefined();
  });

  it("reports commit_no_archive when compact has neither live messages nor a tail", async () => {
    const session = uniqueSession();
    const client = makeClient();
    client.getSessionContext.mockResolvedValue(context([], "SUMMARY"));
    client.commitSession.mockResolvedValue({ status: "skipped", archived: false, reason: "no_messages" });
    const { engine } = makeEngine(client);

    const result = await engine.compact({ sessionId: session, sessionFile: "", tokenBudget: 4096, force: true });

    expect(result).toMatchObject({ ok: true, compacted: false, reason: "commit_no_archive" });
  });

  it("drops the tail when compact archives newer live messages", async () => {
    const session = uniqueSession();
    const client = makeClient();
    client.getSessionContext.mockResolvedValueOnce(context(conversation("m", 6)));
    const { engine } = makeEngine(client);
    await engine.afterTurn!(afterTurnParams(session));

    client.getSessionContext.mockResolvedValue(context([], "NEWER SUMMARY"));
    client.commitSession.mockResolvedValue({
      session_id: session,
      status: "completed",
      task_id: "task-2",
      archive_uri: `viking://session/${session}/history/archive_002`,
      archived: true,
    });
    const result = await engine.compact({ sessionId: session, sessionFile: "", tokenBudget: 4096, force: true });

    expect(result).toMatchObject({
      compacted: true,
      result: { summary: "NEWER SUMMARY", firstKeptEntryId: "archive_002" },
    });
    expect(getRetainedTail(session)).toBeUndefined();
  });

  it("drops the tail on reset", async () => {
    const session = uniqueSession();
    setRetainedTail(session, [msg("m1")]);
    const client = makeClient();
    client.commitSession.mockResolvedValue({
      session_id: session,
      status: "skipped",
      task_id: null,
      archive_uri: null,
      archived: false,
      reason: "no_messages",
      reset_context: true,
    });
    const { engine } = makeEngine(client);

    await expect(engine.commitOVSession({ sessionId: session })).resolves.toBe(true);

    expect(getRetainedTail(session)).toBeUndefined();
  });
});
