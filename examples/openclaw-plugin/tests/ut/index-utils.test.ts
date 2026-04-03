import { describe, expect, it } from "vitest";

import {
  sanitizeOpenVikingAgentIdHeader,
  createSessionAgentResolver,
} from "../../index.js";

describe("sanitizeOpenVikingAgentIdHeader", () => {
  it("keeps clean alphanumeric+dash+underscore strings", () => {
    expect(sanitizeOpenVikingAgentIdHeader("my-agent_v2")).toBe("my-agent_v2");
  });

  it("replaces special characters with underscore", () => {
    expect(sanitizeOpenVikingAgentIdHeader("agent:role:v1")).toBe("agent_role_v1");
  });

  it("collapses multiple underscores", () => {
    expect(sanitizeOpenVikingAgentIdHeader("a::b:::c")).toBe("a_b_c");
  });

  it("strips leading/trailing underscores", () => {
    expect(sanitizeOpenVikingAgentIdHeader("_agent_")).toBe("agent");
  });

  it("returns 'default' for empty string", () => {
    expect(sanitizeOpenVikingAgentIdHeader("")).toBe("default");
  });

  it("returns 'default' for whitespace-only", () => {
    expect(sanitizeOpenVikingAgentIdHeader("   ")).toBe("default");
  });

  it("returns 'ov_agent' for all-symbol input", () => {
    expect(sanitizeOpenVikingAgentIdHeader("@#$%")).toBe("ov_agent");
  });

  it("handles spaces by replacing with underscore", () => {
    expect(sanitizeOpenVikingAgentIdHeader("my agent")).toBe("my_agent");
  });

  it("preserves mixed case", () => {
    expect(sanitizeOpenVikingAgentIdHeader("MyAgent")).toBe("MyAgent");
  });
});

describe("createSessionAgentResolver", () => {
  it("resolves to 'default' when no session info and configAgentId is 'default'", () => {
    const resolver = createSessionAgentResolver("default");
    const result = resolver.resolve();
    expect(result.resolved).toBe("default");
    expect(result.branch).toBe("default_no_session");
  });

  it("resolves to configAgentId when no session info", () => {
    const resolver = createSessionAgentResolver("custom-agent");
    const result = resolver.resolve();
    expect(result.resolved).toBe("custom-agent");
    expect(result.branch).toBe("config_only_fallback");
  });

  it("remembers and resolves session-scoped agent", () => {
    const resolver = createSessionAgentResolver("default");
    resolver.remember({
      sessionId: "session-1",
      agentId: "agent-abc",
    });
    const result = resolver.resolve("session-1");
    expect(result.resolved).toBe("agent-abc");
    expect(result.branch).toBe("session_resolved");
  });

  it("extracts agentId from sessionKey pattern agent:X:...", () => {
    const resolver = createSessionAgentResolver("default");
    resolver.remember({
      sessionKey: "agent:myagent:session123",
    });
    const result = resolver.resolve(undefined, "agent:myagent:session123");
    expect(result.resolved).toBe("myagent");
  });

  it("combines configAgentId with session-scoped agent when config is not 'default'", () => {
    const resolver = createSessionAgentResolver("prefix");
    resolver.remember({
      sessionId: "s1",
      agentId: "worker",
    });
    const result = resolver.resolve("s1");
    expect(result.resolved).toContain("prefix");
    expect(result.resolved).toContain("worker");
  });

  it("returns consistent results for the same session", () => {
    const resolver = createSessionAgentResolver("default");
    resolver.remember({ sessionId: "s1", agentId: "a1" });
    const r1 = resolver.resolve("s1");
    const r2 = resolver.resolve("s1");
    expect(r1.resolved).toBe(r2.resolved);
  });
});
