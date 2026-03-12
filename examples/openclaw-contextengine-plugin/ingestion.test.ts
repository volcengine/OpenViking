import { describe, expect, it, vi } from "vitest";

import { toBatchPayload, writeBatchAndCommit } from "./ingestion.js";

describe("ingestion", () => {
  it("keeps user/assistant/system messages and drops tool messages when disabled", () => {
    const payload = toBatchPayload({
      messages: [
        { role: "system", content: [{ type: "text", text: "S" }] },
        { role: "user", content: [{ type: "text", text: "U" }] },
        { role: "assistant", content: [{ type: "text", text: "A" }] },
        { role: "tool", content: [{ type: "text", text: "T" }] },
      ],
      includeSystemPrompt: true,
      includeToolCalls: false,
      maxBatchMessages: 10,
    });

    expect(payload).toHaveLength(3);
    expect(payload.map((m) => m.role)).toEqual(["system", "user", "assistant"]);
  });

  it("applies maxBatchMessages from tail and supports dedupe window", () => {
    const payload = toBatchPayload({
      messages: [
        { role: "user", content: [{ type: "text", text: "A" }] },
        { role: "user", content: [{ type: "text", text: "B" }] },
        { role: "user", content: [{ type: "text", text: "B" }] },
      ],
      includeSystemPrompt: true,
      includeToolCalls: true,
      maxBatchMessages: 2,
      dedupeWindow: 2,
    });

    expect(payload).toHaveLength(1);
    expect(payload[0]?.content).toBe("B");
  });

  it("writes session messages and commits", async () => {
    const createSession = vi.fn(async () => "s1");
    const addSessionMessage = vi.fn(async () => undefined);
    const commitSession = vi.fn(async () => ({ extractedCount: 2 }));
    const deleteSession = vi.fn(async () => undefined);

    const out = await writeBatchAndCommit(
      {
        createSession,
        addSessionMessage,
        commitSession,
        deleteSession,
      },
      [
        { role: "user", content: "U" },
        { role: "assistant", content: "A" },
      ],
    );

    expect(createSession).toHaveBeenCalledTimes(1);
    expect(addSessionMessage).toHaveBeenCalledTimes(2);
    expect(commitSession).toHaveBeenCalledWith("s1");
    expect(deleteSession).toHaveBeenCalledWith("s1");
    expect(out).toEqual({ extractedCount: 2 });
  });

  it("preserves commit error when deleteSession also fails", async () => {
    const createSession = vi.fn(async () => "s1");
    const addSessionMessage = vi.fn(async () => undefined);
    const commitSession = vi.fn(async () => {
      throw new Error("commit failed");
    });
    const deleteSession = vi.fn(async () => {
      throw new Error("delete failed");
    });

    await expect(
      writeBatchAndCommit(
        {
          createSession,
          addSessionMessage,
          commitSession,
          deleteSession,
        },
        [{ role: "user", content: "U" }],
      ),
    ).rejects.toThrow("commit failed");
  });
});
