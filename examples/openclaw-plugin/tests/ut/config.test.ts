import { describe, expect, it, vi, afterEach } from "vitest";
import { homedir } from "node:os";

import { memoryOpenVikingConfigSchema } from "../../config.js";

const RESOURCE_QUERY_TOOLS = ["ov_search", "ov_read", "ov_multi_read", "ov_list"];

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
    expect(cfg.agent_prefix).toBe("");
    expect(cfg.isolateUserScopeByAgent).toBe(false);
    expect(cfg.isolateAgentScopeByUser).toBe(false);
    expect(cfg.emitStandardDiagnostics).toBe(false);
    expect(cfg.traceRecall).toBe(false);
    expect(cfg.traceRecallPersist).toBe(false);
    expect(cfg.traceRecallDir).toBe(`${homedir()}/.openclaw/openviking/recall-traces`);
    expect(cfg.traceRecallRetentionDays).toBe(14);
    expect(cfg.traceRecallLoadRecentDays).toBe(2);
    expect(cfg.traceRecallMaxEntries).toBe(1000);
    expect(cfg.traceRecallMaxResultsPerSearch).toBe(20);
    expect(cfg.traceRecallPreviewChars).toBe(240);
    expect(cfg.traceRecallQueryMaxChars).toBe(4000);
    expect(cfg.traceRecallQueryMaxDays).toBe(14);
    expect(cfg.traceRecallIncludeContentByDefault).toBe(false);
    expect(cfg.traceRecallIncludeRawUserPreview).toBe(false);
    expect(cfg.recallTargetTypes).toEqual(["user", "agent"]);
    expect(cfg.enableAddResourceTool).toBe(false);
    expect(cfg.enabledTools).toEqual([
      "add_skill",
      "ov_search",
      "ov_read",
      "ov_multi_read",
      "ov_list",
      "memory_recall",
      "ov_recall_trace",
      "memory_store",
      "memory_forget",
      "ov_archive_search",
      "ov_archive_expand",
      "openviking_tool_result_read",
      "openviking_tool_result_search",
      "openviking_tool_result_list",
    ]);
    expect(cfg.disabledTools).toEqual(["add_resource"]);
  });

  it("keeps add_resource disabled by default and enables it only by explicit config", () => {
    expect(memoryOpenVikingConfigSchema.parse({}).enableAddResourceTool).toBe(false);
    expect(memoryOpenVikingConfigSchema.parse({ enableAddResourceTool: false }).enableAddResourceTool).toBe(false);
    expect(memoryOpenVikingConfigSchema.parse({ enableAddResourceTool: true }).enableAddResourceTool).toBe(true);
  });

  it("supports resource-query-only tool configuration", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      enabledTools: ["resource_query"],
    });
    expect(cfg.enabledTools).toEqual(RESOURCE_QUERY_TOOLS);
    expect(cfg.disabledTools).toEqual(["add_resource"]);
  });

  it("keeps recall trace query tool enabled when trace recording is enabled with a narrow legacy allowlist", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      traceRecall: true,
      enabledTools: ["resource_query"],
    });

    expect(cfg.enabledTools).toEqual([...RESOURCE_QUERY_TOOLS, "ov_recall_trace"]);
    expect(cfg.disabledTools).toEqual(["add_resource"]);
  });

  it("allows disabledTools to explicitly hide recall trace even when trace recording is enabled", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      traceRecall: true,
      enabledTools: ["resource_query"],
      disabledTools: ["recall_trace"],
    });

    expect(cfg.enabledTools).toEqual(RESOURCE_QUERY_TOOLS);
    expect(cfg.disabledTools).toEqual(["ov_recall_trace", "add_resource"]);
  });

  it("supports disabling memory-related tool group while keeping resource query tools", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      disabledTools: "memory",
    });
    expect(cfg.enabledTools).toEqual(expect.arrayContaining(["ov_search", "ov_read"]));
    expect(cfg.enabledTools).not.toEqual(expect.arrayContaining(["memory_recall", "memory_store", "memory_forget"]));
    expect(cfg.disabledTools).toEqual(["memory_recall", "memory_store", "memory_forget", "add_resource"]);
  });

  it("rejects unknown tool selectors", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ enabledTools: ["ov_search", "not_a_tool"] }),
    ).toThrow("unknown tool selectors");
  });

  it("defaults recallMaxInjectedChars to the 4000-character memory budget", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.recallMaxInjectedChars).toBe(4000);
    expect(cfg.recallTokenBudget).toBe(4000);
  });

  it("honors explicit recallPreferAbstract=false without changing the default", () => {
    const cfgDefault = memoryOpenVikingConfigSchema.parse({});
    const cfgFalse = memoryOpenVikingConfigSchema.parse({ recallPreferAbstract: false });
    const cfgTrue = memoryOpenVikingConfigSchema.parse({ recallPreferAbstract: true });
    expect(cfgDefault.recallPreferAbstract).toBe(false);
    expect(cfgFalse.recallPreferAbstract).toBe(false);
    expect(cfgTrue.recallPreferAbstract).toBe(true);
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

  it("falls back to an empty prefix for empty agent_prefix", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ agent_prefix: "  " });
    expect(cfg.agent_prefix).toBe("");
  });

  it("normalizes legacy 'default' agent_prefix to an empty prefix", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ agent_prefix: "default" });
    expect(cfg.agent_prefix).toBe("");
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

  it("accepts trace recall configuration keys and clamps numeric bounds", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      traceRecall: true,
      traceRecallPersist: true,
      traceRecallDir: " /tmp/openviking-traces ",
      traceRecallRetentionDays: -1,
      traceRecallLoadRecentDays: -5,
      traceRecallMaxEntries: 0,
      traceRecallMaxResultsPerSearch: 0,
      traceRecallPreviewChars: 10,
      traceRecallQueryMaxChars: 100,
      traceRecallQueryMaxDays: 0,
      traceRecallIncludeContentByDefault: true,
      traceRecallIncludeRawUserPreview: true,
    });

    expect(cfg.traceRecall).toBe(true);
    expect(cfg.traceRecallPersist).toBe(true);
    expect(cfg.traceRecallDir).toBe("/tmp/openviking-traces");
    expect(cfg.traceRecallRetentionDays).toBe(1);
    expect(cfg.traceRecallLoadRecentDays).toBe(0);
    expect(cfg.traceRecallMaxEntries).toBe(1);
    expect(cfg.traceRecallMaxResultsPerSearch).toBe(1);
    expect(cfg.traceRecallPreviewChars).toBe(20);
    expect(cfg.traceRecallQueryMaxChars).toBe(200);
    expect(cfg.traceRecallQueryMaxDays).toBe(1);
    expect(cfg.traceRecallIncludeContentByDefault).toBe(true);
    expect(cfg.traceRecallIncludeRawUserPreview).toBe(true);
  });

  it("normalizes recallTargetTypes from arrays and comma-separated strings", () => {
    const fromArray = memoryOpenVikingConfigSchema.parse({
      recallTargetTypes: [" user ", "agent", "user", ""],
    });
    expect(fromArray.recallTargetTypes).toEqual(["user", "agent"]);

    const fromString = memoryOpenVikingConfigSchema.parse({
      recallTargetTypes: "resource, session\nagent",
    });
    expect(fromString.recallTargetTypes).toEqual(["resource", "session", "agent"]);
  });

  it("rejects unknown recallTargetTypes instead of falling back to defaults", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ recallTargetTypes: ["user", "project"] }),
    ).toThrow("recallTargetTypes contains unknown resource types: project");
  });

  it("treats empty recallTargetTypes as the backward-compatible memory recall set", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ recallTargetTypes: [] });
    expect(cfg.recallTargetTypes).toEqual(["user", "agent"]);
  });

  it("keeps deprecated recallResources as an additive compatibility switch when recallTargetTypes is unset", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ recallResources: true });
    expect(cfg.recallTargetTypes).toEqual(["user", "agent", "resource"]);
  });

  it("does not let deprecated recallResources override explicit resource-only recallTargetTypes", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      recallResources: true,
      recallTargetTypes: ["resource"],
    });
    expect(cfg.recallTargetTypes).toEqual(["resource"]);
  });
});
