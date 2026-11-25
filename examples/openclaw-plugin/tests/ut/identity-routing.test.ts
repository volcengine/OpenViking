import { createHash } from "node:crypto";
import { describe, expect, it, vi } from "vitest";

import {
  createSessionAgentResolver,
  openClawSessionRefToOvStorageId,
  openClawSessionToOvStorageId,
  resolveOpenVikingActorPeerId,
  resolveOpenVikingMessagePeerId,
  sanitizeOpenVikingAgentIdHeader,
} from "../../routing/identity-routing.js";

describe("identity routing registry", () => {
  it("keeps the storage ID stable for UUIDs, host session keys, and Windows paths", () => {
    const uuid = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890";
    const sessionKey = "agent:myagent:session123";
    const hash = createHash("sha256").update(sessionKey).digest("hex");
    const windowsId = "C:\\Users\\test";

    expect(openClawSessionToOvStorageId(`  ${uuid}  `, sessionKey)).toBe(uuid.toLowerCase());
    expect(openClawSessionRefToOvStorageId(` ${uuid} `)).toBe(uuid.toLowerCase());
    expect(openClawSessionToOvStorageId("plain-session", undefined)).toBe("plain-session");
    expect(openClawSessionRefToOvStorageId("plain-session")).toBe("plain-session");
    expect(openClawSessionToOvStorageId("plain-session", sessionKey)).toBe(hash);
    expect(openClawSessionToOvStorageId(undefined, sessionKey)).toBe(hash);
    expect(openClawSessionRefToOvStorageId(sessionKey)).toBe(hash);
    expect(openClawSessionToOvStorageId(windowsId, undefined)).toBe(
      createHash("sha256").update(`openclaw-session:${windowsId}`).digest("hex"),
    );
    expect(openClawSessionRefToOvStorageId(windowsId)).toBe(
      createHash("sha256").update(windowsId).digest("hex"),
    );
    expect(() => openClawSessionToOvStorageId("", "")).toThrow("need sessionId or sessionKey");
    expect(() => openClawSessionToOvStorageId(undefined, undefined)).toThrow("need sessionId or sessionKey");
    expect(() => openClawSessionRefToOvStorageId("   ")).toThrow("empty session ref");
  });

  it("normalizes the actor peer header while preserving its valid characters", () => {
    for (const [input, expected] of [
      ["my-Agent_v2", "my-Agent_v2"],
      [" _a::b:::c_ ", "a_b_c"],
      ["my agent", "my_agent"],
      [" ", "default"],
      ["@#$%", "ov_agent"],
    ]) {
      expect(sanitizeOpenVikingAgentIdHeader(input)).toBe(expected);
    }
  });

  it("routes sender scope to the sender peer for messages and data-plane requests", () => {
    expect(resolveOpenVikingMessagePeerId({
      peerRole: "sender",
      role: "user",
      senderPeerId: "sender-42",
    })).toBe("sender-42");
    expect(resolveOpenVikingMessagePeerId({
      peerRole: "sender",
      role: "assistant",
      senderPeerId: "sender-42",
    })).toBeUndefined();
    expect(resolveOpenVikingActorPeerId({
      peerRole: "sender",
      senderPeerId: "sender-42",
    })).toBe("sender-42");
  });

  it("widens to the unscoped request with a warning when the sender is missing", () => {
    const warn = vi.fn();
    expect(resolveOpenVikingActorPeerId({ peerRole: "sender", warn })).toBeUndefined();
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("no sender identity"));

    warn.mockClear();
    expect(resolveOpenVikingActorPeerId({ peerRole: "sender", senderPeerId: "sender-42", warn })).toBe("sender-42");
    expect(resolveOpenVikingActorPeerId({ peerRole: "assistant", assistantPeerId: "agent", warn })).toBe("agent");
    expect(resolveOpenVikingActorPeerId({ peerRole: "none", warn })).toBeUndefined();
    expect(warn).not.toHaveBeenCalled();
  });

  it("resolves session-scoped agents with aliases and config prefix unchanged", () => {
    const resolver = createSessionAgentResolver("prefix");
    resolver.remember({ sessionId: "s1", sessionKey: "agent:worker:session123", agentId: "agent-abc" });

    expect(resolver.resolve("s1")).toMatchObject({
      resolved: "prefix_agent-abc",
      branch: "session_resolved",
      fromExplicitBinding: true,
    });
    expect(resolver.resolve(undefined, "agent:worker:session123")).toMatchObject({
      resolved: "prefix_agent-abc",
      branch: "session_resolved",
    });
    expect(createSessionAgentResolver("default").resolve()).toMatchObject({
      resolved: "main",
      branch: "default_no_session",
    });
    expect(createSessionAgentResolver("prefix").resolve()).toMatchObject({
      resolved: "prefix_main",
      branch: "config_only_fallback",
    });
    expect(createSessionAgentResolver("prefix").resolve(undefined, "agent:worker:session123")).toMatchObject({
      resolved: "prefix_worker",
      branch: "session_resolved",
    });
  });
});
