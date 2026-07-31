import { describe, expect, it } from "vitest";

import {
  createSessionAgentResolver,
  openClawSessionRefToOvStorageId,
  openClawSessionToOvStorageId,
  sanitizeOpenVikingAgentIdHeader,
} from "../../routing/identity-routing.js";

describe("identity routing registry", () => {
  it("keeps OpenClaw session to OpenViking storage id behavior byte-compatible", () => {
    const uuid = "A1B2C3D4-E5F6-7890-ABCD-EF1234567890";
    expect(openClawSessionToOvStorageId(uuid, undefined)).toBe(uuid.toLowerCase());
    expect(openClawSessionToOvStorageId("plain-session", undefined)).toBe("plain-session");
    expect(openClawSessionToOvStorageId(undefined, "agent:myagent:session123")).toMatch(/^[a-f0-9]{64}$/);
    expect(openClawSessionToOvStorageId("C:\\Users\\test", undefined)).toMatch(/^[a-f0-9]{64}$/);
    expect(() => openClawSessionToOvStorageId("", "")).toThrow("need sessionId or sessionKey");
  });

  it("normalizes hook/tool session refs in the concrete routing module", () => {
    expect(openClawSessionRefToOvStorageId(" A1B2C3D4-E5F6-7890-ABCD-EF1234567890 ")).toBe(
      "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    );
    expect(openClawSessionRefToOvStorageId("safe-session")).toBe("safe-session");
    expect(openClawSessionRefToOvStorageId("C:\\bad\\path")).toMatch(/^[a-f0-9]{64}$/);
    expect(() => openClawSessionRefToOvStorageId("   ")).toThrow("empty session ref");
  });

  it("sanitizes OpenViking actor peer headers in the concrete routing module", () => {
    expect(sanitizeOpenVikingAgentIdHeader("agent:role:v1")).toBe("agent_role_v1");
    expect(sanitizeOpenVikingAgentIdHeader("   ")).toBe("default");
    expect(sanitizeOpenVikingAgentIdHeader("@#$%")).toBe("ov_agent");
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
    expect(createSessionAgentResolver("prefix").resolve(undefined, "agent:worker:session123")).toMatchObject({
      resolved: "prefix_worker",
      branch: "session_resolved",
    });
  });
});
