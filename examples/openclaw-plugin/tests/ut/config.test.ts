import { describe, expect, it, vi, afterEach } from "vitest";
import { homedir } from "node:os";
import { join, resolve as resolvePath } from "node:path";

import { memoryOpenVikingConfigSchema } from "../../config.js";

describe("memoryOpenVikingConfigSchema.parse()", () => {
  const originalEnv = { ...process.env };

  afterEach(() => {
    process.env = { ...originalEnv };
  });

  it("empty object uses all defaults", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({});
    expect(cfg.mode).toBe("local");
    expect(cfg.port).toBe(1933);
    expect(cfg.recallLimit).toBe(6);
    expect(cfg.recallScoreThreshold).toBe(0.15);
    expect(cfg.autoCapture).toBe(true);
    expect(cfg.autoRecall).toBe(true);
    expect(cfg.recallPreferAbstract).toBe(false);
    expect(cfg.recallTokenBudget).toBe(2000);
    expect(cfg.commitTokenThreshold).toBe(20000);
    expect(cfg.ingestReplyAssist).toBe(false);
    expect(cfg.captureMode).toBe("semantic");
    expect(cfg.captureMaxLength).toBe(24000);
    expect(cfg.recallMaxContentChars).toBe(500);
    expect(cfg.agentId).toBe("default");
    expect(cfg.emitStandardDiagnostics).toBe(false);
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

  it("clamps port below 1 to 1", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ port: 0 });
    expect(cfg.port).toBe(1);
  });

  it("clamps port above 65535 to 65535", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ port: 99999 });
    expect(cfg.port).toBe(65535);
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

  it("expands tilde in configPath", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      configPath: "~/custom/ov.conf",
    });
    const expected = resolvePath(join(homedir(), "custom", "ov.conf"));
    expect(cfg.configPath).toBe(expected);
  });

  it("throws on invalid captureMode", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ captureMode: "fast" }),
    ).toThrow('captureMode must be "semantic" or "keyword"');
  });

  it("local mode auto-generates baseUrl from port", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      mode: "local",
      port: 9999,
    });
    expect(cfg.baseUrl).toBe("http://127.0.0.1:9999");
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
    expect(cfg1.mode).toBe("local");
    expect(cfg2.mode).toBe("local");
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

  it("resolves agentId from configured value", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ agentId: "  my-agent  " });
    expect(cfg.agentId).toBe("my-agent");
  });

  it("falls back to 'default' for empty agentId", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ agentId: "  " });
    expect(cfg.agentId).toBe("default");
  });
});
