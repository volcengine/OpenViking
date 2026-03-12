import { describe, expect, it } from "vitest";

import { parseConfig } from "./config.js";

describe("parseConfig", () => {
  it("applies required defaults", () => {
    const cfg = parseConfig({});
    expect(cfg.mode).toBe("local");
    expect(cfg.connection.baseUrl).toBe("http://127.0.0.1:1933");
    expect(cfg.connection.timeoutMs).toBe(15000);
    expect(cfg.connection.apiKey).toBe("");
    expect(cfg.connection.agentId).toBe("default");
    expect(cfg.retrieval.enabled).toBe(true);
    expect(cfg.retrieval.lastNUserMessages).toBe(5);
    expect(cfg.retrieval.skipGreeting).toBe(true);
    expect(cfg.retrieval.minQueryChars).toBe(4);
    expect(cfg.retrieval.targetUri).toBe("viking://user/memories");
    expect(cfg.profileInjection.enabled).toBe(true);
    expect(cfg.profileInjection.qualityGateMinScore).toBe(0.7);
    expect(cfg.profileInjection.maxChars).toBe(1200);
    expect(cfg.ingestion.writeMode).toBe("compact_batch");
  });

  it("rejects non-object top-level config when provided", () => {
    expect(() => parseConfig("bad" as never)).toThrow(/config must be an object/i);
    expect(() => parseConfig(123 as never)).toThrow(/config must be an object/i);
    expect(() => parseConfig([] as never)).toThrow(/config must be an object/i);
  });

  it("rejects non-object retrieval/ingestion sections", () => {
    expect(() => parseConfig({ connection: "bad" } as never)).toThrow(/connection must be an object/i);
    expect(() => parseConfig({ retrieval: "bad" } as never)).toThrow(/retrieval must be an object/i);
    expect(() => parseConfig({ ingestion: 123 } as never)).toThrow(/ingestion must be an object/i);
  });

  it("validates enum fields", () => {
    expect(() =>
      parseConfig({
        mode: "invalid",
      } as never),
    ).toThrow(/mode/i);

    expect(() =>
      parseConfig({
        retrieval: { injectMode: "bad" },
      } as never),
    ).toThrow(/injectMode/i);

    expect(() =>
      parseConfig({
        ingestion: { writeMode: "bad" },
      } as never),
    ).toThrow(/writeMode/i);
  });

  it("requires retrieval.enabled to be boolean when provided", () => {
    expect(() =>
      parseConfig({
        retrieval: { enabled: "false" },
      } as never),
    ).toThrow(/enabled must be a boolean/i);
  });

  it("requires retrieval.skipGreeting to be boolean when provided", () => {
    expect(() =>
      parseConfig({
        retrieval: { skipGreeting: "true" },
      } as never),
    ).toThrow(/skipGreeting must be a boolean/i);
  });

  it("requires profileInjection.enabled to be boolean when provided", () => {
    expect(() =>
      parseConfig({
        profileInjection: { enabled: "true" },
      } as never),
    ).toThrow(/profileInjection\.enabled must be a boolean/i);
  });

  it("clamps implemented numeric fields", () => {
    const cfg = parseConfig({
      connection: {
        timeoutMs: 1,
      },
      retrieval: {
        scoreThreshold: 9,
        lastNUserMessages: -3,
        minQueryChars: -2,
      },
      profileInjection: {
        qualityGateMinScore: 2,
        maxChars: 20,
      },
      ingestion: {
        maxBatchMessages: -8,
      },
    });

    expect(cfg.retrieval.scoreThreshold).toBe(1);
    expect(cfg.retrieval.lastNUserMessages).toBe(1);
    expect(cfg.retrieval.minQueryChars).toBe(1);
    expect(cfg.connection.timeoutMs).toBe(1000);
    expect(cfg.profileInjection.qualityGateMinScore).toBe(1);
    expect(cfg.profileInjection.maxChars).toBe(200);
    expect(cfg.ingestion.maxBatchMessages).toBe(1);
  });
});
