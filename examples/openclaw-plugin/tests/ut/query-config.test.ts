import { describe, expect, it } from "vitest";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { memoryOpenVikingConfigSchema } from "../../config.js";
import {
  RuntimeQueryConfigStore,
  normalizeRuntimeQueryParams,
  resolveSessionQueryConfigKey,
} from "../../query-config.js";

describe("normalizeRuntimeQueryParams", () => {
  it("clamps values and normalizes resourceTypes", () => {
    const normalized = normalizeRuntimeQueryParams({
      recallLimit: 999,
      candidateMultiplier: 0,
      candidateLimit: 2,
      scoreThreshold: 2,
      maxInjectedChars: 10,
      ovSearchLimit: 999,
      resourceTypes: "resource,user",
    });

    expect(normalized.params).toMatchObject({
      recallLimit: 50,
      candidateMultiplier: 1,
      candidateLimit: 50,
      scoreThreshold: 1,
      maxInjectedChars: 100,
      ovSearchLimit: 100,
      resourceTypes: ["resource", "user"],
    });
    expect(normalized.warnings).toContain("candidateLimit was raised to recallLimit");
  });

  it("rejects non-viking targetUri", () => {
    expect(() => normalizeRuntimeQueryParams({ targetUri: "https://example.com/doc" })).toThrow("targetUri must start with viking://");
  });
});

describe("resolveSessionQueryConfigKey", () => {
  it("prefers ovSessionId then sessionId then sessionKey", () => {
    expect(resolveSessionQueryConfigKey({ ovSessionId: "ov1", sessionId: "s1", sessionKey: "k1" })).toBe("ov:ov1");
    expect(resolveSessionQueryConfigKey({ sessionId: "s1", sessionKey: "k1" })).toBe("session:s1");
    expect(resolveSessionQueryConfigKey({ sessionKey: "k1" })).toBe("key:k1");
    expect(resolveSessionQueryConfigKey({})).toBeUndefined();
  });
});

describe("RuntimeQueryConfigStore", () => {
  it("merges default, static, claw, session, and request layers with field sources", async () => {
    const cfg = memoryOpenVikingConfigSchema.parse({
      recallLimit: 6,
      recallScoreThreshold: 0.15,
      recallMaxInjectedChars: 4000,
      recallTargetTypes: ["user", "agent"],
    });
    const store = RuntimeQueryConfigStore.createInMemory(cfg);

    await store.set("claw", { peerId: "assistant-a" }, { recallLimit: 8, scoreThreshold: 0.25, resourceTypes: ["resource"] });
    await store.set("session", { peerId: "assistant-a", ovSessionId: "ov-session" }, { recallLimit: 3 });

    const effective = await store.getEffective(
      { peerId: "assistant-a", ovSessionId: "ov-session" },
      { scoreThreshold: 0.05 },
    );

    expect(effective.recallLimit).toBe(3);
    expect(effective.scoreThreshold).toBe(0.05);
    expect(effective.resourceTypes).toEqual(["resource"]);
    expect(effective.candidateLimit).toBe(20);
    expect(effective.sources.recallLimit).toBe("session");
    expect(effective.sources.scoreThreshold).toBe("request");
    expect(effective.sources.resourceTypes).toBe("claw");
  });

  it("preserves lower-priority explicit candidateLimit when a higher-priority layer changes only recallLimit", async () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ recallLimit: 6 });
    const store = RuntimeQueryConfigStore.createInMemory(cfg);

    await store.set("claw", { peerId: "assistant-a" }, { candidateLimit: 80 });
    await store.set("session", { peerId: "assistant-a", sessionId: "s1" }, { recallLimit: 3 });

    const effective = await store.getEffective({ peerId: "assistant-a", sessionId: "s1" });

    expect(effective.recallLimit).toBe(3);
    expect(effective.candidateLimit).toBe(80);
    expect(effective.sources.recallLimit).toBe("session");
    expect(effective.sources.candidateLimit).toBe("claw");
  });

  it("persists runtime config and reloads modified files", async () => {
    const dir = await mkdtemp(join(tmpdir(), "ov-query-config-"));
    try {
      const path = join(dir, "runtime-query-config.json");
      const cfg = memoryOpenVikingConfigSchema.parse({ recallLimit: 6 });
      const store = new RuntimeQueryConfigStore({ staticConfig: cfg, path });
      await store.load();
      await store.set("claw", { peerId: "assistant-a" }, { recallLimit: 9 });

      const saved = JSON.parse(await readFile(path, "utf8"));
      expect(saved.claws["assistant-a"].params.recallLimit).toBe(9);

      saved.claws["assistant-a"].params.recallLimit = 4;
      await writeFile(path, JSON.stringify(saved), "utf8");
      await store.reloadIfChanged({ force: true });

      const effective = await store.getEffective({ peerId: "assistant-a" });
      expect(effective.recallLimit).toBe(4);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  it("serializes writes behind an in-flight initial load", async () => {
    const dir = await mkdtemp(join(tmpdir(), "ov-query-config-"));
    try {
      const path = join(dir, "runtime-query-config.json");
      const cfg = memoryOpenVikingConfigSchema.parse({ recallLimit: 6 });
      await writeFile(path, JSON.stringify({
        schemaVersion: "1.0",
        updatedAt: Date.now(),
        claws: { "assistant-a": { params: { recallLimit: 9 }, updatedAt: Date.now() } },
        sessions: {},
      }), "utf8");
      const store = new RuntimeQueryConfigStore({ staticConfig: cfg, path });

      const loadPromise = store.load();
      const setPromise = store.set("claw", { peerId: "assistant-a" }, { recallLimit: 2 });
      await Promise.all([loadPromise, setPromise]);

      const effective = await store.getEffective({ peerId: "assistant-a" });
      expect(effective.recallLimit).toBe(2);
      expect(effective.sources.recallLimit).toBe("claw");
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  it("keeps the persistence queue usable after a transient write failure", async () => {
    const dir = await mkdtemp(join(tmpdir(), "ov-query-config-"));
    try {
      const blockedParent = join(dir, "blocked-parent");
      const path = join(blockedParent, "runtime-query-config.json");
      const cfg = memoryOpenVikingConfigSchema.parse({ recallLimit: 6 });
      await writeFile(blockedParent, "not a directory", "utf8");
      const store = new RuntimeQueryConfigStore({ staticConfig: cfg, path });

      await expect(store.set("claw", { peerId: "assistant-a" }, { recallLimit: 2 })).rejects.toThrow();
      await rm(blockedParent, { force: true });

      await expect(store.set("claw", { peerId: "assistant-a" }, { recallLimit: 3 })).resolves.toBeDefined();
      const saved = JSON.parse(await readFile(path, "utf8"));
      expect(saved.claws["assistant-a"].params.recallLimit).toBe(3);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });

  it("unsets one field and resets scoped runtime config", async () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ recallLimit: 6, recallScoreThreshold: 0.15 });
    const store = RuntimeQueryConfigStore.createInMemory(cfg);

    await store.set("session", { peerId: "assistant-a", sessionId: "s1" }, { recallLimit: 2, scoreThreshold: 0.4 });
    expect((await store.getEffective({ peerId: "assistant-a", sessionId: "s1" })).recallLimit).toBe(2);

    await store.unset("session", { peerId: "assistant-a", sessionId: "s1" }, ["recallLimit"]);
    const afterUnset = await store.getEffective({ peerId: "assistant-a", sessionId: "s1" });
    expect(afterUnset.recallLimit).toBe(6);
    expect(afterUnset.scoreThreshold).toBe(0.4);

    await store.reset("session", { peerId: "assistant-a", sessionId: "s1" });
    const afterReset = await store.getEffective({ peerId: "assistant-a", sessionId: "s1" });
    expect(afterReset.recallLimit).toBe(6);
    expect(afterReset.scoreThreshold).toBe(0.15);
  });

  it("merges incremental set calls within the same runtime scope", async () => {
    const cfg = memoryOpenVikingConfigSchema.parse({ recallLimit: 6, recallScoreThreshold: 0.15 });
    const store = RuntimeQueryConfigStore.createInMemory(cfg);
    const ctx = { peerId: "assistant-a", sessionId: "s1" };

    await store.set("session", ctx, { recallLimit: 2 });
    await store.set("session", ctx, { scoreThreshold: 0.4 });

    const effective = await store.getEffective(ctx);
    expect(effective.recallLimit).toBe(2);
    expect(effective.scoreThreshold).toBe(0.4);
    expect(effective.sources.recallLimit).toBe("session");
    expect(effective.sources.scoreThreshold).toBe("session");
  });
});
