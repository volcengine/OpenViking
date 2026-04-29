import { describe, expect, it, vi, afterEach } from "vitest";

import { memoryOpenVikingConfigSchema } from "../../config.js";

describe("memoryOpenVikingConfigSchema.parse()", () => {
  const originalEnv = { ...process.env };

  afterEach(() => {
    process.env = { ...originalEnv };
  });

  it("empty object uses all defaults", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.mode).toBe("remote");
    expect(cfg.recallLimit).toBe(6);
    expect(cfg.recallScoreThreshold).toBe(0.15);
    expect(cfg.autoCapture).toBe(true);
    expect(cfg.autoRecall).toBe(true);
    expect(cfg.recallPreferAbstract).toBe(false);
    expect(cfg.recallMaxInjectedChars).toBe(4000);
    expect(cfg.recallTokenBudget).toBe(4000);
    expect(cfg.commitTokenThreshold).toBe(20000);
    expect(cfg.captureMode).toBe("semantic");
    expect(cfg.captureMaxLength).toBe(24000);
    expect(cfg.recallMaxContentChars).toBe(5000);
    expect(cfg.agent_prefix).toBe("default");
    expect(cfg.isolateUserScopeByAgent).toBe(false);
    expect(cfg.isolateAgentScopeByUser).toBe(false);
    expect(cfg.emitStandardDiagnostics).toBe(false);
  });

  it("defaults recallMaxInjectedChars to the 4000-character memory budget", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.recallMaxInjectedChars).toBe(4000);
    expect(cfg.recallTokenBudget).toBe(4000);
  });

  it("uses recallMaxInjectedChars as the canonical auto-recall character budget", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      recallMaxInjectedChars: 1234,
    });
    expect(cfg.recallMaxInjectedChars).toBe(1234);
    expect(cfg.recallTokenBudget).toBe(1234);
  });

  it("falls back to deprecated recallTokenBudget when recallMaxInjectedChars is unset", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      recallTokenBudget: 2345,
    });
    expect(cfg.recallMaxInjectedChars).toBe(2345);
    expect(cfg.recallTokenBudget).toBe(2345);
  });

  it("prefers recallMaxInjectedChars over deprecated recallTokenBudget", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      recallMaxInjectedChars: 3456,
      recallTokenBudget: 2345,
    });
    expect(cfg.recallMaxInjectedChars).toBe(3456);
    expect(cfg.recallTokenBudget).toBe(3456);
  });

  it("remote mode preserves custom baseUrl", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      mode: "remote",
      baseUrl: "http://example.com:9000",
    });
    expect(cfg.mode).toBe("remote");
    expect(cfg.baseUrl).toBe("http://example.com:9000");
  });

  it("throws on unknown config keys", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ foo: 1 }),
    ).toThrow("unknown keys");
  });

  it("resolves environment variables in apiKey", () => {
    process.env.TEST_OV_API_KEY = "sk-test-key-123";
    const cfg = memoryOpenVikingConfigSchema.parse({
      apiKey: "${TEST_OV_API_KEY}",
    });
    expect(cfg.apiKey).toBe("sk-test-key-123");
    delete process.env.TEST_OV_API_KEY;
  });

  it("throws when referenced env var is not set", () => {
    delete process.env.NOT_SET_OV_VAR;
    expect(() =>
      memoryOpenVikingConfigSchema.parse({
        apiKey: "${NOT_SET_OV_VAR}",
      }),
    ).toThrow("NOT_SET_OV_VAR");
  });

  it("clamps negative recallScoreThreshold to 0", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      recallScoreThreshold: -0.5,
    });
    expect(cfg.recallScoreThreshold).toBe(0);
  });

  it("clamps recallScoreThreshold above 1 to 1", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      recallScoreThreshold: 1.5,
    });
    expect(cfg.recallScoreThreshold).toBe(1);
  });

  it("throws on invalid captureMode", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ captureMode: "fast" }),
    ).toThrow('captureMode must be "semantic" or "keyword"');
  });

  it("trims trailing slashes from baseUrl", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      mode: "remote",
      baseUrl: "http://example.com:9000///",
    });
    expect(cfg.baseUrl).toBe("http://example.com:9000");
  });

  it("clamps recallLimit to minimum 1", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ recallLimit: 0 });
    expect(cfg.recallLimit).toBe(1);
  });

  it("clamps timeoutMs to minimum 1000", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ timeoutMs: 100 });
    expect(cfg.timeoutMs).toBe(1000);
  });

  it("treats undefined/null as empty config", () => {
    const cfg1 = memoryOpenVikingConfigSchema.parse(undefined);
    const cfg2 = memoryOpenVikingConfigSchema.parse(null);
    expect(cfg1.mode).toBe("remote");
    expect(cfg2.mode).toBe("remote");
  });

  it("accepts valid captureMode values", () => {
    const cfgSemantic = memoryOpenVikingConfigSchema.parse({ captureMode: "semantic" });
    expect(cfgSemantic.captureMode).toBe("semantic");
    const cfgKeyword = memoryOpenVikingConfigSchema.parse({ captureMode: "keyword" });
    expect(cfgKeyword.captureMode).toBe("keyword");
  });

  it("clamps captureMaxLength within bounds", () => {
    const cfgLow = memoryOpenVikingConfigSchema.parse({ captureMaxLength: 10 });
    expect(cfgLow.captureMaxLength).toBe(200);
    const cfgHigh = memoryOpenVikingConfigSchema.parse({ captureMaxLength: 999999 });
    expect(cfgHigh.captureMaxLength).toBe(200000);
  });

  it("clamps recallMaxContentChars within bounds", () => {
    const cfgLow = memoryOpenVikingConfigSchema.parse({ recallMaxContentChars: 1 });
    expect(cfgLow.recallMaxContentChars).toBe(50);
    const cfgHigh = memoryOpenVikingConfigSchema.parse({ recallMaxContentChars: 99999 });
    expect(cfgHigh.recallMaxContentChars).toBe(10000);
  });

  it("resolves agent_prefix from configured value", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ agent_prefix: "  my-agent  " });
    expect(cfg.agent_prefix).toBe("my-agent");
  });

  it("falls back to 'default' for empty agent_prefix", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ agent_prefix: "  " });
    expect(cfg.agent_prefix).toBe("default");
  });

  it("migrates legacy agentId to agent_prefix", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ agentId: "legacy-agent" });
    expect(cfg.agent_prefix).toBe("legacy-agent");
  });

  it("agent_prefix takes precedence over legacy agentId", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ agentId: "old", agent_prefix: "new" });
    expect(cfg.agent_prefix).toBe("new");
  });

  it("parses accountId and trims whitespace", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ accountId: "  acct-123  " });
    expect(cfg.accountId).toBe("acct-123");
  });

  it("defaults accountId to empty string when missing", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.accountId).toBe("");
  });

  it("defaults accountId to empty string for whitespace-only value", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ accountId: "   " });
    expect(cfg.accountId).toBe("");
  });

  it("parses userId and trims whitespace", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ userId: "  user-456  " });
    expect(cfg.userId).toBe("user-456");
  });

  it("defaults userId to empty string when missing", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.userId).toBe("");
  });

  it("default user-key flow does not require accountId, userId, or agentScopeMode", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      baseUrl: "http://127.0.0.1:1933",
      apiKey: "sk-user",
      agent_prefix: "coding-agent",
    });
    expect(cfg.accountId).toBe("");
    expect(cfg.userId).toBe("");
    expect(cfg.agentScopeMode).toBe("agent");
    expect(cfg.isolateUserScopeByAgent).toBe(false);
    expect(cfg.isolateAgentScopeByUser).toBe(false);
  });

  it("defaults namespace policy to the current server-side false/false policy", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.agentScopeMode).toBe("agent");
    expect(cfg.isolateUserScopeByAgent).toBe(false);
    expect(cfg.isolateAgentScopeByUser).toBe(false);
  });

  it("maps deprecated agentScopeMode 'agent' to false/false namespace policy", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ agentScopeMode: "agent" });
    expect(cfg.agentScopeMode).toBe("agent");
    expect(cfg.isolateUserScopeByAgent).toBe(false);
    expect(cfg.isolateAgentScopeByUser).toBe(false);
  });

  it("falls back to user_agent for invalid agentScopeMode", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ agentScopeMode: "invalid" });
    expect(cfg.agentScopeMode).toBe("agent");
    expect(cfg.isolateUserScopeByAgent).toBe(false);
    expect(cfg.isolateAgentScopeByUser).toBe(false);
  });

  it("maps explicit deprecated agentScopeMode 'user_agent' to false/true namespace policy", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ agentScopeMode: "user_agent" });
    expect(cfg.agentScopeMode).toBe("user_agent");
    expect(cfg.isolateUserScopeByAgent).toBe(false);
    expect(cfg.isolateAgentScopeByUser).toBe(true);
  });

  it("explicit namespace policy overrides deprecated agentScopeMode", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      agentScopeMode: "agent",
      isolateUserScopeByAgent: true,
      isolateAgentScopeByUser: true,
    });
    expect(cfg.agentScopeMode).toBe("agent");
    expect(cfg.isolateUserScopeByAgent).toBe(true);
    expect(cfg.isolateAgentScopeByUser).toBe(true);
  });

  it("accepts deprecated serverAuthMode without exposing it in parsed config", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ serverAuthMode: "trusted" });
    expect("serverAuthMode" in cfg).toBe(false);
  });

  it("defaults recallResources to false", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.recallResources).toBe(false);
  });

  it("enables recallResources when set to true", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ recallResources: true });
    expect(cfg.recallResources).toBe(true);
  });

  it("recallResources only accepts boolean true", () => {
    const cfg1 = memoryOpenVikingConfigSchema.parse({ recallResources: "true" });
    expect(cfg1.recallResources).toBe(false);
    const cfg2 = memoryOpenVikingConfigSchema.parse({ recallResources: 1 });
    expect(cfg2.recallResources).toBe(false);
  });
});
