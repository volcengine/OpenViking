import { describe, expect, it, vi } from "vitest";

import type { OpenVikingClient } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { createMemoryOpenVikingContextEngine } from "../../context-engine.js";

// Mock the transport boundary, not the lifecycle service: swallowed client
// failures must make these tests fail, even when the callback itself resolves.
function makeEngine(hostVersion: string | undefined, overrides = {}) {
  const logger = { info: vi.fn(), warn: vi.fn(), error: vi.fn() };
  const client = {
    addSessionMessage: vi.fn().mockResolvedValue(undefined),
    getSession: vi.fn().mockResolvedValue({ pending_tokens: 100_000 }),
    commitSession: vi.fn().mockResolvedValue({ status: "accepted" }),
  };
  const getClient = vi.fn().mockResolvedValue(client);
  const engine = createMemoryOpenVikingContextEngine({
    id: "openviking",
    name: "Context Engine (OpenViking)",
    version: "test",
    hostVersion,
    cfg: memoryOpenVikingConfigSchema.parse({
      mode: "remote",
      baseUrl: "http://127.0.0.1:1933",
      autoCapture: true,
      ...overrides,
    }),
    logger,
    getClient: getClient as () => Promise<OpenVikingClient>,
    resolveAgentId: () => "agent",
  });
  return { engine, client, getClient, logger };
}

const messages = [
  { role: "user", content: "Remember that my preferred editor is Vim." },
  { role: "assistant", content: "I will remember your editor preference." },
];
const turn = { advancementKey: "k1", sessionId: "session-1", messages };
const afterTurn = { ...turn, sessionFile: "unused", prePromptMessageCount: 0 };

function deferred() {
  let resolve!: () => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<void>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

describe("context-engine capture ownership", () => {
  it("keeps the transcript declarations required by 8.1+ hosts", () => {
    expect(makeEngine("2026.9.3").engine.info.transcriptSemantics).toEqual({
      currentTurnFence: "before-current-turn-entry-v1",
      turnAdvancementIdempotency: "atomic-idempotent-v1",
    });
  });

  it.each(["2026.5.27"])("keeps afterTurn-only hosts working (%s)", async (version) => {
    const { engine, client } = makeEngine(version);
    await engine.afterTurn!(afterTurn);
    expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
    expect(client.commitSession).toHaveBeenCalledTimes(1);
  });

  it.each(["2026.9.2"])(
    "does not recapture afterTurn messages in commitTurn on %s", async (version) => {
      const { engine, client } = makeEngine(version);
      // Old tool-loop checkpoint, then finalize checkpoint, then durable ACK.
      await engine.afterTurn!({ ...afterTurn, messages: messages.slice(0, 1) });
      await engine.afterTurn!({ ...afterTurn, prePromptMessageCount: 1 });
      await expect(engine.commitTurn(turn)).resolves.toEqual({ status: "committed" });
      expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
      expect(client.getSession).toHaveBeenCalledTimes(2);
      await expect(engine.commitTurn(turn)).resolves.toEqual({ status: "duplicate" });
      expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
    },
  );

  it.each(["2026.9.3", "2026.9.3-1"])(
    "captures closed turns before ACK on %s", async (version) => {
      const { engine, client } = makeEngine(version);
      await expect(engine.commitTurn(turn)).resolves.toEqual({ status: "committed" });
      expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
      expect(client.addSessionMessage.mock.calls.map((call) => call[1])).toEqual(["user", "assistant"]);
      expect(client.commitSession).toHaveBeenCalledTimes(1);
      await expect(engine.commitTurn(turn)).resolves.toEqual({ status: "duplicate" });
      expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
    },
  );

  it("keeps standalone afterTurn capture on 9.3 alongside durable sessions", async () => {
    const { engine, client } = makeEngine("2026.9.3");
    await engine.afterTurn!({ ...afterTurn, sessionId: "standalone" });
    await engine.commitTurn(turn);
    expect(client.addSessionMessage).toHaveBeenCalledTimes(4);
    expect(client.commitSession).toHaveBeenCalledTimes(2);
  });

  it.each([undefined, "0.0.0", "2026.9.3-beta.1"])("does not ACK an unknown capture owner (%s)", async (version) => {
    const { engine, client } = makeEngine(version);
    await expect(engine.commitTurn(turn)).rejects.toThrow("cannot select turn capture");
    await expect(engine.commitTurn(turn)).rejects.toThrow("cannot select turn capture");
    expect(client.addSessionMessage).not.toHaveBeenCalled();
    await engine.afterTurn!(afterTurn);
    expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
  });
});

describe("durable capture failures and skips", () => {
  it.each(["addSessionMessage", "commitSession"] as const)(
    "rejects %s failure and permits retry with the same key", async (stage) => {
      const { engine, client } = makeEngine("2026.9.3");
      const operation = client[stage];
      operation.mockRejectedValueOnce(new Error(`${stage} unavailable`));
      await expect(engine.commitTurn(turn)).rejects.toThrow(`${stage} unavailable`);
      await expect(engine.commitTurn(turn)).resolves.toEqual({ status: "committed" });
      await expect(engine.commitTurn(turn)).resolves.toEqual({ status: "duplicate" });
      expect(client.commitSession).toHaveBeenCalled();
    },
  );

  it("preserves best-effort errors in the legacy callback", async () => {
    const { engine, client, logger } = makeEngine("2026.9.1");
    client.addSessionMessage.mockRejectedValueOnce(new Error("offline"));
    await expect(engine.afterTurn!(afterTurn)).resolves.toBeUndefined();
    expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining("offline"));
  });

  it.each([
    { config: { autoCapture: false }, params: turn },
    { config: {}, params: { ...turn, isHeartbeat: true } },
    { config: { bypassSessionPatterns: ["session-1"] }, params: turn },
  ])("ACKs intentional capture skips", async ({ config, params }) => {
    const { engine, getClient } = makeEngine("2026.9.3", config);
    await expect(engine.commitTurn(params)).resolves.toEqual({ status: "committed" });
    expect(getClient).not.toHaveBeenCalled();
  });
});

describe("concurrent advancement-key delivery", () => {
  it("waits for one capture before either caller ACKs", async () => {
    const { engine, client } = makeEngine("2026.9.3");
    const gate = deferred();
    const entered = deferred();
    client.addSessionMessage.mockImplementationOnce(() => { entered.resolve(); return gate.promise; });
    const first = engine.commitTurn(turn);
    await entered.promise;
    const second = engine.commitTurn(turn);
    let secondSettled = false;
    void second.then(() => { secondSettled = true; });
    await Promise.resolve();
    expect(secondSettled).toBe(false);
    expect(client.addSessionMessage).toHaveBeenCalledTimes(1);
    gate.resolve();
    await expect(Promise.all([first, second])).resolves.toEqual([
      { status: "committed" }, { status: "duplicate" },
    ]);
    expect(client.addSessionMessage).toHaveBeenCalledTimes(2);
    expect(client.commitSession).toHaveBeenCalledTimes(1);
  });

  it("rejects every waiter on failure and permits a later retry", async () => {
    const { engine, client } = makeEngine("2026.9.3");
    const gate = deferred();
    const entered = deferred();
    client.addSessionMessage.mockImplementationOnce(() => { entered.resolve(); return gate.promise; });
    const first = engine.commitTurn(turn);
    await entered.promise;
    const second = engine.commitTurn(turn);
    const results = Promise.allSettled([first, second]);
    const error = new Error("write failed");
    gate.reject(error);
    expect(await results).toEqual([
      { status: "rejected", reason: error }, { status: "rejected", reason: error },
    ]);
    await expect(engine.commitTurn(turn)).resolves.toEqual({ status: "committed" });
    expect(client.addSessionMessage).toHaveBeenCalledTimes(3);
  });
});
