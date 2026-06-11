import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import {
  RecallTraceJsonlStore,
  RecallTraceMemoryStore,
  RecallTraceRecorder,
  normalizeResourceTypes,
  resolveRecallSearchPlan,
  type RecallTraceEntry,
} from "../../recall-trace.js";

const tempDirs: string[] = [];

function makeTempDir(): string {
  const dir = join(tmpdir(), `openviking-recall-trace-${process.pid}-${Date.now()}-${tempDirs.length}`);
  mkdirSync(dir, { recursive: true });
  tempDirs.push(dir);
  return dir;
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    rmSync(dir, { recursive: true, force: true });
  }
});

function makeTrace(overrides: Partial<RecallTraceEntry> = {}): RecallTraceEntry {
  return {
    schemaVersion: "1.0",
    traceId: "trace-1",
    ts: 1_000,
    sessionId: "session-1",
    sessionKey: "key-1",
    ovSessionId: "ov-1",
    agentId: "agent-1",
    source: "auto_recall",
    operationType: "semantic_find",
    resourceTypes: ["resource", "session", "user", "agent"],
    trigger: {
      query: "how to inspect recall trace",
    },
    searches: [],
    selected: [],
    stats: {
      candidateCount: 0,
      selectedCount: 0,
      injectedCount: 0,
    },
    ...overrides,
  };
}

describe("normalizeResourceTypes()", () => {
  it("defaults missing, empty array, and empty string to the backward-compatible memory recall set", () => {
    expect(normalizeResourceTypes(undefined)).toEqual(["user", "agent"]);
    expect(normalizeResourceTypes([])).toEqual(["user", "agent"]);
    expect(normalizeResourceTypes("  ")).toEqual(["user", "agent"]);
  });

  it("normalizes comma-separated strings, trims entries, and deduplicates", () => {
    expect(normalizeResourceTypes(" user,agent\nuser ")).toEqual(["user", "agent"]);
  });

  it("rejects unknown resource types instead of falling back to defaults", () => {
    expect(() => normalizeResourceTypes(["user", "project"])).toThrow(
      "invalid resourceTypes: project",
    );
  });
});

describe("resolveRecallSearchPlan()", () => {
  it("builds the default backward-compatible memory recall search plan", () => {
    const plan = resolveRecallSearchPlan(undefined, {
      ovSessionId: "ov-session-1",
      agentId: "agent-1",
    });

    expect(plan.searches).toEqual([
      { resourceType: "user", targetUri: "viking://user/memories" },
      { resourceType: "agent", targetUri: "viking://agent/memories" },
    ]);
    expect(plan.skipped).toEqual([]);
    expect(plan.resourceTypes).toEqual(["user", "agent"]);
  });

  it("builds session/user/agent searches only when explicitly requested", () => {
    const plan = resolveRecallSearchPlan(["resource", "session", "user", "agent"], {
      ovSessionId: "ov-session-1",
      agentId: "agent-1",
    });

    expect(plan.searches).toEqual([
      { resourceType: "resource", targetUri: "viking://resources" },
      { resourceType: "session", targetUri: "viking://session/ov-session-1/history" },
      { resourceType: "user", targetUri: "viking://user/memories" },
      { resourceType: "agent", targetUri: "viking://agent/memories" },
    ]);
  });

  it("skips session search without failing the whole plan when ovSessionId is missing", () => {
    const plan = resolveRecallSearchPlan(["session", "user"], { agentId: "agent-1" });

    expect(plan.searches).toEqual([
      { resourceType: "user", targetUri: "viking://user/memories" },
    ]);
    expect(plan.skipped).toEqual([
      { resourceType: "session", reason: "missing_session" },
    ]);
    expect(plan.resourceTypes).toEqual(["session", "user"]);
  });
});

describe("RecallTraceMemoryStore", () => {
  it("keeps a bounded ring buffer and returns matching traces by timestamp descending", () => {
    const store = new RecallTraceMemoryStore(2);
    store.record(makeTrace({ traceId: "old", ts: 1, sessionId: "session-a" }));
    store.record(makeTrace({ traceId: "newer", ts: 3, sessionId: "session-a" }));
    store.record(makeTrace({ traceId: "new", ts: 2, sessionId: "session-a" }));

    const result = store.query({ turn: "all", sessionId: "session-a", limit: 10 });

    expect(result.entries.map((entry) => entry.traceId)).toEqual(["newer", "new"]);
  });

  it("supports latest, source, session identifiers, traceId, time, and resourceTypes filters", () => {
    const store = new RecallTraceMemoryStore(10);
    store.record(makeTrace({
      traceId: "auto-user",
      ts: 10,
      source: "auto_recall",
      sessionId: "session-a",
      resourceTypes: ["user"],
    }));
    store.record(makeTrace({
      traceId: "tool-resource",
      ts: 20,
      source: "ov_search",
      sessionId: "session-b",
      ovSessionId: "ov-b",
      resourceTypes: ["resource"],
    }));
    store.record(makeTrace({
      traceId: "auto-agent",
      ts: 30,
      source: "auto_recall",
      sessionKey: "key-c",
      resourceTypes: ["agent"],
    }));

    expect(store.query({ turn: "latest", source: "auto_recall", limit: 10 }).entries.map((e) => e.traceId))
      .toEqual(["auto-agent"]);
    expect(store.query({ turn: "all", ovSessionId: "ov-b", limit: 10 }).entries.map((e) => e.traceId))
      .toEqual(["tool-resource"]);
    expect(store.query({ turn: "all", traceId: "auto-user", limit: 10 }).entries.map((e) => e.traceId))
      .toEqual(["auto-user"]);
    expect(store.query({ turn: "all", since: 15, until: 35, resourceTypes: ["agent"], limit: 10 }).entries.map((e) => e.traceId))
      .toEqual(["auto-agent"]);
  });
});

describe("RecallTraceJsonlStore", () => {
  it("persists traces by day and lets a new store query them after flush", async () => {
    const dir = makeTempDir();
    const store = new RecallTraceJsonlStore({ dir });
    await store.append(makeTrace({ traceId: "persisted", ts: Date.UTC(2026, 5, 1), sessionId: "session-jsonl" }));
    await store.flush();

    const reloaded = new RecallTraceJsonlStore({ dir });
    const result = await reloaded.query({ turn: "all", sessionId: "session-jsonl", limit: 10 });

    expect(result.entries.map((entry) => entry.traceId)).toEqual(["persisted"]);
    expect(result.lookupLayer).toBe("persistent");
    expect(result.warnings).toEqual([]);
  });

  it("does not persist raw user text previews unless explicitly enabled", async () => {
    const dir = makeTempDir();
    const store = new RecallTraceJsonlStore({ dir });
    await store.append(makeTrace({
      traceId: "redacted-preview",
      ts: Date.UTC(2026, 5, 1),
      trigger: {
        query: "safe query",
        rawUserTextPreview: "private user wording",
      },
    }));
    await store.flush();

    const reloaded = new RecallTraceJsonlStore({ dir });
    const result = await reloaded.query({ turn: "all", traceId: "redacted-preview", limit: 10 });

    expect(result.entries[0]!.trigger.query).toBe("safe query");
    expect(result.entries[0]!.trigger.rawUserTextPreview).toBeUndefined();
  });

  it("skips corrupted JSONL lines and returns warnings with valid entries", async () => {
    const dir = makeTempDir();
    writeFileSync(
      join(dir, "1970-01-01.jsonl"),
      [
        "not-json",
        JSON.stringify(makeTrace({ traceId: "valid", ts: 1_000, sessionId: "session-jsonl" })),
        "",
      ].join("\n"),
      "utf8",
    );

    const store = new RecallTraceJsonlStore({ dir });
    const result = await store.query({ turn: "all", sessionId: "session-jsonl", since: 0, until: 86_400_000, limit: 10 });

    expect(result.entries.map((entry) => entry.traceId)).toEqual(["valid"]);
    expect(result.warnings.some((warning) => warning.includes("Skipping corrupted recall trace line"))).toBe(true);
  });
});

describe("RecallTraceRecorder", () => {
  it("does not create a persistent directory when persistence is disabled", async () => {
    const parent = makeTempDir();
    const traceDir = join(parent, "disabled-traces");
    const recorder = new RecallTraceRecorder({
      memoryMaxEntries: 10,
      persist: false,
      traceDir,
    });

    recorder.record(makeTrace({ traceId: "memory-only", sessionId: "session-memory" }));
    await recorder.flush();

    expect(existsSync(traceDir)).toBe(false);
    expect(recorder.query({ turn: "all", sessionId: "session-memory", limit: 10 }).entries.map((entry) => entry.traceId))
      .toEqual(["memory-only"]);
  });

  it("keeps memory traces queryable when JSONL append fails", async () => {
    const parent = makeTempDir();
    const blockedPath = join(parent, "not-a-directory");
    writeFileSync(blockedPath, "block mkdir", "utf8");
    const recorder = new RecallTraceRecorder({
      memoryMaxEntries: 10,
      persist: true,
      traceDir: blockedPath,
    });

    recorder.record(makeTrace({ traceId: "best-effort", sessionId: "session-memory" }));
    const flushResult = await recorder.flush();

    expect(flushResult.warnings.length).toBeGreaterThan(0);
    expect(recorder.query({ turn: "all", sessionId: "session-memory", limit: 10 }).entries.map((entry) => entry.traceId))
      .toEqual(["best-effort"]);
  });

  it("queries memory first and falls back to persisted traces when memory has no match", async () => {
    const dir = makeTempDir();
    const writer = new RecallTraceRecorder({
      memoryMaxEntries: 10,
      persist: true,
      traceDir: dir,
    });
    writer.record(makeTrace({ traceId: "persisted-only", sessionId: "persisted-session", ts: Date.now() }));
    await writer.flush();

    const reader = new RecallTraceRecorder({
      memoryMaxEntries: 10,
      persist: true,
      traceDir: dir,
    });
    const result = await reader.queryWithFallback({ turn: "all", sessionId: "persisted-session", limit: 10 });

    expect(result.lookupLayer).toBe("persistent");
    expect(result.entries.map((entry) => entry.traceId)).toEqual(["persisted-only"]);
  });

  it("can durably record and flush a trace before a tool returns", async () => {
    const dir = makeTempDir();
    const recorder = new RecallTraceRecorder({
      memoryMaxEntries: 10,
      persist: true,
      traceDir: dir,
    });

    await recorder.recordAndFlush(makeTrace({
      traceId: "durable-before-return",
      sessionId: "durable-session",
      ts: Date.UTC(2026, 5, 1),
    }));

    const freshReader = new RecallTraceRecorder({
      memoryMaxEntries: 10,
      persist: true,
      traceDir: dir,
    });
    const result = await freshReader.queryWithFallback({ turn: "all", sessionId: "durable-session", limit: 10 });

    expect(result.lookupLayer).toBe("persistent");
    expect(result.entries.map((entry) => entry.traceId)).toEqual(["durable-before-return"]);
  });
});
