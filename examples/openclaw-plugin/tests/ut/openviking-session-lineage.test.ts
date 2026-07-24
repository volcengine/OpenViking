import { describe, expect, it } from "vitest";

import { OpenVikingSessionLineageStore } from "../../plugin/openviking-session-lineage.js";

describe("OpenVikingSessionLineageStore", () => {
  it("returns at most two recent predecessors from the same session key", async () => {
    let now = 1_000;
    const store = new OpenVikingSessionLineageStore({ now: () => now });

    for (const [previousSessionId, nextSessionId] of [
      ["session-1", "session-2"],
      ["session-2", "session-3"],
      ["session-3", "session-4"],
    ]) {
      await store.record({
        sessionId: previousSessionId,
        sessionKey: "agent:main:dashboard:one",
        nextSessionId,
        nextSessionKey: "agent:main:dashboard:one",
        reason: "daily",
        transcriptArchived: true,
      });
      now += 1;
    }

    await expect(store.getPredecessorSessionIds(
      "agent:main:dashboard:one",
      "session-4",
    )).resolves.toEqual(["session-3", "session-2"]);
    await expect(store.getPredecessorSessionIds(
      "agent:main:dashboard:other",
      "session-4",
    )).resolves.toEqual([]);
  });

  it("ignores expired, unarchived, cross-key, and explicit-new transitions", async () => {
    let now = 1_000;
    const store = new OpenVikingSessionLineageStore({
      maxAgeMs: 100,
      now: () => now,
    });

    await store.record({
      sessionId: "expired",
      sessionKey: "session-key",
      nextSessionId: "current",
      reason: "daily",
      transcriptArchived: true,
    });
    now = 1_101;
    await expect(store.getPredecessorSessionIds("session-key", "current")).resolves.toEqual([]);

    for (const transition of [
      { nextSessionKey: "other-key", reason: "daily", transcriptArchived: true },
      { nextSessionKey: "session-key", reason: "daily", transcriptArchived: false },
      { nextSessionKey: "session-key", reason: "new", transcriptArchived: true },
    ]) {
      await store.record({
        sessionId: "previous",
        sessionKey: "session-key",
        nextSessionId: "current",
        ...transition,
      });
    }
    await expect(store.getPredecessorSessionIds("session-key", "current")).resolves.toEqual([]);
  });
});
