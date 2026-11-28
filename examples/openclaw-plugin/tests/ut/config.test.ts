import { afterEach, describe, expect, it, vi } from "vitest";

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
    expect(cfg.recallResources).toBe(false);
    expect(cfg.recallMaxInjectedChars).toBe(4000);
    expect(cfg.recallTokenBudget).toBe(4000);
    expect(cfg.commitTokenThresholdRatio).toBe(0.5);
    expect(cfg.commitRetentionMode).toBe("message_count");
    expect(cfg.commitKeepRecentCount).toBe(10);
    expect(cfg.captureMode).toBe("semantic");
    expect(cfg.captureMaxLength).toBe(24000);
    expect(cfg.autoRecallTimeoutMs).toBe(15000);
    expect(cfg.recallMaxContentChars).toBe(5000);
    expect(cfg.peer_role).toBe("none");
    expect(cfg.peer_prefix).toBe("");
    expect(cfg.emitStandardDiagnostics).toBe(false);
    expect(cfg.traceRecall).toBe(false);
    expect(cfg.traceRecallPersist).toBe(false);
    expect(cfg.traceRecallDir).toContain(".openclaw/openviking/recall-traces");
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
    expect(cfg.headers).toEqual({});
    expect(cfg.enableAddResourceTool).toBe(false);
    expect(cfg.enabledTools).toContain("ov_search");
    expect(cfg.enabledTools).toContain("ov_read");
    expect(cfg.enabledTools).not.toContain("add_resource");
    expect(cfg.disabledTools).toContain("add_resource");
    expect(cfg.agentExperience.enabled).toBe(false);
  });

  it("tolerates the deprecated commitTokenThreshold key and ignores it", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ commitTokenThreshold: 20000 });
    expect(cfg.commitTokenThresholdRatio).toBe(0.5);
  });

  it("expands enabled and disabled tool groups", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      enabledTools: ["resource_query", "memory"],
      disabledTools: "memory_forget",
    });
    expect(cfg.enabledTools).toEqual([
      "ov_search",
      "ov_read",
      "ov_multi_read",
      "ov_list",
      "memory_recall",
      "memory_store",
    ]);
    expect(cfg.disabledTools).toEqual(["memory_forget", "add_resource"]);
  });

  it("rejects unpublished experience tool selectors", () => {
    for (const selector of ["experience", "search_experience", "read_experience"]) {
      expect(() =>
        memoryOpenVikingConfigSchema.parse({ enabledTools: [selector] }),
      ).toThrow("unknown tool selectors");
    }
  });

  it("throws on unknown tool selectors", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ enabledTools: ["resource_query", "nope"] }),
    ).toThrow("unknown tool selectors");
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ disabledTools: "nope" }),
    ).toThrow("unknown tool selectors");
  });

  it("parses and clamps recall trace settings", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      traceRecall: true,
      traceRecallPersist: true,
      traceRecallDir: "~/custom-traces",
      traceRecallRetentionDays: 0,
      traceRecallLoadRecentDays: -1,
      traceRecallMaxEntries: 2_000_000,
      traceRecallMaxResultsPerSearch: 0,
      traceRecallPreviewChars: 5,
      traceRecallQueryMaxChars: 100,
      traceRecallQueryMaxDays: 9999,
      traceRecallIncludeContentByDefault: true,
      traceRecallIncludeRawUserPreview: true,
    });

    expect(cfg.traceRecall).toBe(true);
    expect(cfg.traceRecallPersist).toBe(true);
    expect(cfg.traceRecallDir).toContain("custom-traces");
    expect(cfg.traceRecallRetentionDays).toBe(1);
    expect(cfg.traceRecallLoadRecentDays).toBe(0);
    expect(cfg.traceRecallMaxEntries).toBe(1_000_000);
    expect(cfg.traceRecallMaxResultsPerSearch).toBe(1);
    expect(cfg.traceRecallPreviewChars).toBe(20);
    expect(cfg.traceRecallQueryMaxChars).toBe(200);
    expect(cfg.traceRecallQueryMaxDays).toBe(3650);
    expect(cfg.traceRecallIncludeContentByDefault).toBe(true);
    expect(cfg.traceRecallIncludeRawUserPreview).toBe(true);
  });

  it("selects the canonical recall budget before the legacy alias", () => {
    for (const [input, limit] of [
      [{ recallMaxInjectedChars: 1234 }, 1234],
      [{ recallTokenBudget: 2345 }, 2345],
      [{ recallTokenBudget: 2345, recallMaxInjectedChars: 3456 }, 3456],
    ] as const) {
      expect(memoryOpenVikingConfigSchema.parse(input)).toMatchObject({
        recallMaxInjectedChars: limit, recallTokenBudget: limit,
      });
    }
    expect(memoryOpenVikingConfigSchema.parse({ recallPreferAbstract: true }).recallPreferAbstract).toBe(true);
  });

  it("keeps a remote base URL and strips trailing slashes", () => {
    expect(memoryOpenVikingConfigSchema.parse({
      mode: "remote", baseUrl: "http://example.com:9000///",
    })).toMatchObject({ mode: "remote", baseUrl: "http://example.com:9000" });
  });

  it("throws on unknown config keys", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ foo: 1 }),
    ).toThrow("unknown keys");
  });

  it("parses string-valued configured headers", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      headers: {
        openviking: "i18n-instance",
        token: "root-token",
      },
    });

    expect(cfg.headers).toEqual({
      openviking: "i18n-instance",
      token: "root-token",
    });
  });

  it("throws on non-string configured header values", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({
        headers: {
          openviking: 123,
        },
      }),
    ).toThrow("openviking config headers.openviking must be a string");
  });

  it("throws on unknown agentExperience keys", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({
        agentExperience: { autoRecall: true },
      }),
    ).toThrow("agentExperience has unknown keys");
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

  it.each(["unknown", "", null, 0, false])("rejects invalid commitRetentionMode %j", (value) => {
    expect(() => memoryOpenVikingConfigSchema.parse({ commitRetentionMode: value }))
      .toThrow('commitRetentionMode must be "message_count" or "turn_budget"');
  });

  it("throws on invalid captureMode", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ captureMode: "fast" }),
    ).toThrow('captureMode must be "semantic" or "keyword"');
  });

  it("bounds numeric recall, capture and transport settings", () => {
    const low = memoryOpenVikingConfigSchema.parse({
      recallScoreThreshold: -0.5, recallLimit: 0, timeoutMs: 100,
      autoRecallTimeoutMs: 100, captureMaxLength: 10, recallMaxContentChars: 1,
    });
    expect(low).toMatchObject({
      recallScoreThreshold: 0, recallLimit: 1, timeoutMs: 1000,
      autoRecallTimeoutMs: 1000, captureMaxLength: 200, recallMaxContentChars: 50,
    });
    const high = memoryOpenVikingConfigSchema.parse({
      recallScoreThreshold: 1.5, autoRecallTimeoutMs: 999999,
      captureMaxLength: 999999, recallMaxContentChars: 99999,
    });
    expect(high).toMatchObject({
      recallScoreThreshold: 1, autoRecallTimeoutMs: 300000,
      captureMaxLength: 200000, recallMaxContentChars: 10000,
    });
    expect(memoryOpenVikingConfigSchema.parse({
      autoRecallTimeoutMs: 30000, captureMode: "keyword",
    })).toMatchObject({ autoRecallTimeoutMs: 30000, captureMode: "keyword" });
  });

  it("uses the same default settings for null and undefined config", () => {
    const defaults = memoryOpenVikingConfigSchema.parse({});
    expect(memoryOpenVikingConfigSchema.parse(undefined)).toEqual(defaults);
    expect(memoryOpenVikingConfigSchema.parse(null)).toEqual(defaults);
  });

  it("accepts explicit peer_role values", () => {
    expect(memoryOpenVikingConfigSchema.parse({ peer_role: "none" }).peer_role).toBe("none");
    expect(memoryOpenVikingConfigSchema.parse({ peer_role: "assistant" }).peer_role).toBe("assistant");
    expect(memoryOpenVikingConfigSchema.parse({ peer_role: "sender" }).peer_role).toBe("sender");
  });

  it("normalizes the legacy person peer_role alias to sender", () => {
    expect(memoryOpenVikingConfigSchema.parse({ peer_role: "person" }).peer_role).toBe("sender");
  });

  it("throws on invalid peer_role", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ peer_role: "agent" }),
    ).toThrow('peer_role must be "none", "assistant", or "sender"');
  });

  it("trims the explicit identity and respects the legacy default prefix", () => {
    expect(memoryOpenVikingConfigSchema.parse({
      accountId: "  acct-123  ", userId: "  user-456  ", peer_prefix: "  my-agent  ",
    })).toMatchObject({
      accountId: "acct-123", userId: "user-456", peer_prefix: "my-agent", peer_role: "none",
    });
    expect(memoryOpenVikingConfigSchema.parse({
      accountId: "   ", userId: "   ", peer_prefix: "default",
    })).toMatchObject({ accountId: "", userId: "", peer_prefix: "" });
    expect(memoryOpenVikingConfigSchema.parse({ peer_prefix: "  " }).peer_prefix).toBe("");
  });

  it("default user-key flow does not require accountId or userId", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      baseUrl: "http://127.0.0.1:1933",
      apiKey: "sk-user",
      peer_role: "assistant",
      peer_prefix: "coding-agent",
    });
    expect(cfg.accountId).toBe("");
    expect(cfg.userId).toBe("");
  });

  it("accepts deprecated serverAuthMode without exposing it in parsed config", () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ serverAuthMode: "trusted" });
    expect("serverAuthMode" in cfg).toBe(false);
  });

  it("only recognizes a boolean true for the legacy recallResources flag", () => {
    for (const input of [{}, { recallResources: "true" }, { recallResources: 1 }]) {
      expect(memoryOpenVikingConfigSchema.parse(input).recallTargetTypes).toEqual(["user", "agent"]);
    }
  });

  it("normalizes recallTargetTypes from arrays and comma-separated strings", () => {
    const fromArray = memoryOpenVikingConfigSchema.parse({
      recallTargetTypes: [" user ", "agent", "user", ""],
    });
    expect(fromArray.recallTargetTypes).toEqual(["user", "agent"]);

    const fromString = memoryOpenVikingConfigSchema.parse({
      recallTargetTypes: "resource, user\nagent",
    });
    expect(fromString.recallTargetTypes).toEqual(["resource", "user", "agent"]);
  });

  it("rejects unknown recallTargetTypes instead of falling back to defaults", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ recallTargetTypes: ["user", "project"] }),
    ).toThrow("recallTargetTypes contains unknown resource types: project");
  });

  it("rejects session recallTargetTypes because session history is not a semantic recall target", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({ recallTargetTypes: ["session"] }),
    ).toThrow("recallTargetTypes contains unknown resource types: session");
  });

  it("normalizes recallEventsDecayProtection by trimming and defaults to empty", () => {
    const configured = memoryOpenVikingConfigSchema.parse({ recallEventsDecayProtection: " 7d " });
    expect(configured.recallEventsDecayProtection).toBe("7d");

    const unset = memoryOpenVikingConfigSchema.parse({});
    expect(unset.recallEventsDecayProtection).toBe("");
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

describe("memoryOpenVikingConfigSchema.parse() — apiKey SecretRef (#3522)", () => {
  const originalEnv = { ...process.env };
  let tmpDir: string | undefined;

  afterEach(async () => {
    process.env = { ...originalEnv };
    vi.restoreAllMocks();
    if (tmpDir) {
      await import("node:fs/promises").then((fs) =>
        fs.rm(tmpDir, { recursive: true, force: true }).catch(() => void 0),
      );
    }
  });

  it("plain string + ${ENV_VAR} interpolation still works (backward compat)", () => {
    process.env.OV_KEY = "plain-old-key";
    const cfg = memoryOpenVikingConfigSchema.parse({ apiKey: "${OV_KEY}" });
    expect(cfg.apiKey).toBe("plain-old-key");
  });

  it("SecretRef env source reads the named env var", () => {
    process.env.MY_OV_KEY = "from-env-secret";
    const cfg = memoryOpenVikingConfigSchema.parse({
      apiKey: { source: "env", id: "MY_OV_KEY" },
    });
    expect(cfg.apiKey).toBe("from-env-secret");
  });

  it("SecretRef env source errors when the variable is missing instead of silently empty", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({
        apiKey: { source: "env", id: "UNDEFINED_VAR_XYZ" },
      }),
    ).toThrow(/UNDEFINED_VAR_XYZ.*not set or empty/);
  });

  it("SecretRef file source reads the file, expands ~, and trims whitespace", async () => {
    const fs = await import("node:fs/promises");
    const os = await import("node:os");
    const path = await import("node:path");
    tmpDir = await fs.mkdtemp(path.join(os.tmpdir(), "ov-secret-"));
    const keyPath = path.join(tmpDir, "ov.key");
    await fs.writeFile(keyPath, "  the-file-secret\n\n");

    const cfg = memoryOpenVikingConfigSchema.parse({ apiKey: { source: "file", id: keyPath } });
    expect(cfg.apiKey).toBe("the-file-secret");
  });

  it("SecretRef file source surfaces a readable error when the file is missing", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({
        apiKey: { source: "file", id: "/no/such/path/openviking-3522.key" },
      }),
    ).toThrow(/file source.*no\/such\/path/);
  });

  it("SecretRef exec source is rejected with a not-supported error", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({
        apiKey: { source: "exec", provider: "my-vault", id: "secret/openviking/apiKey" },
      }),
    ).toThrow(/exec source is not supported in the packaged plugin/);
  });

  it("SecretRef validates required fields (unknown source, missing id)", () => {
    expect(() =>
      memoryOpenVikingConfigSchema.parse({
        apiKey: { source: "magic", id: "x" } as unknown as import("../../config.js").OpenVikingSecretRef,
      }),
    ).toThrow(/unknown source.*magic/);
    expect(() =>
      memoryOpenVikingConfigSchema.parse({
        apiKey: { source: "env" } as unknown as import("../../config.js").OpenVikingSecretRef,
      }),
    ).toThrow(/SecretRef requires a non-empty string "id"/);
  });

  it("falls back to OPENVIKING_API_KEY env var only when apiKey key is absent", () => {
    process.env.OPENVIKING_API_KEY = "fallback-key";
    // Explicitly present but empty string → still used as-is, NOT replaced by env fallback.
    const cfg1 = memoryOpenVikingConfigSchema.parse({ apiKey: "" });
    expect(cfg1.apiKey).toBe("");
    // Completely missing → env fallback kicks in.
    const cfg2 = memoryOpenVikingConfigSchema.parse({});
    expect(cfg2.apiKey).toBe("fallback-key");
  });
});

describe("cloud compression configuration", () => {
  afterEach(() => vi.unstubAllEnvs());
  it("accepts server configuration and environment override", () => {
    vi.stubEnv('OPENVIKING_RECALL_COMPRESS', undefined);
    expect(memoryOpenVikingConfigSchema.parse({ recallCompress: 'server' }).recallCompress).toBe('server');
    vi.stubEnv('OPENVIKING_RECALL_COMPRESS', 'auto');
    expect(memoryOpenVikingConfigSchema.parse({ recallCompress: 'server' }).recallCompress).toBe('auto');
  });
});
