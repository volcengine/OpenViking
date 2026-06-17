import { describe, expect, it, vi } from "vitest";

import {
  estimateTokenCount,
  buildMemoryLines,
  buildMemoryLinesWithBudget,
} from "../../auto-recall.js";
import { buildAutoRecallContext } from "../../auto-recall.js";
import type { FindResultItem } from "../../client.js";
import { memoryOpenVikingConfigSchema } from "../../config.js";
import { RecallTraceMemoryStore } from "../../recall-trace.js";

function makeMemory(overrides?: Partial<FindResultItem>): FindResultItem {
  return {
    uri: "viking://user/memories/test-1",
    level: 2,
    abstract: "Test memory abstract",
    category: "core",
    score: 0.85,
    ...overrides,
  };
}

describe("estimateTokenCount", () => {
  it("returns 0 for empty string", () => {
    expect(estimateTokenCount("")).toBe(0);
  });

  it("keeps the legacy ASCII estimate close to ceil(chars/4)", () => {
    expect(estimateTokenCount("hello")).toBe(2); // ceil(5/4)
    expect(estimateTokenCount("abcd")).toBe(1); // ceil(4/4)
    expect(estimateTokenCount("abcde")).toBe(2); // ceil(5/4)
  });

  it("uses CJK-aware weighting for non-Latin text and emoji", () => {
    expect(estimateTokenCount("你好世界")).toBe(6);
    expect(estimateTokenCount("A你🙂")).toBe(4);
  });

  it("handles long text", () => {
    const text = "a".repeat(1000);
    expect(estimateTokenCount(text)).toBe(250);
  });
});

describe("buildMemoryLines", () => {
  it("formats memories with category and content", async () => {
    const memories = [
      makeMemory({ category: "preferences", abstract: "User prefers Python" }),
      makeMemory({ category: "facts", abstract: "Works at TechCorp" }),
    ];
    const readFn = vi.fn();

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
    });

    expect(lines).toHaveLength(2);
    expect(lines[0]).toBe("- [preferences] User prefers Python");
    expect(lines[1]).toBe("- [facts] Works at TechCorp");
  });

  it("includes uri metadata when requested", async () => {
    const memories = [
      makeMemory({
        uri: "viking://user/default/memories/projects/openclaw/autorecall_filename_contract.md",
        category: "",
        abstract: "Filename carries semantic meaning.",
      }),
    ];
    const readFn = vi.fn();

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
      includeUri: true,
    });

    expect(lines).toEqual([
      [
        "- [memory]",
        "  <uri>viking://user/default/memories/projects/openclaw/autorecall_filename_contract.md</uri>",
        "  Filename carries semantic meaning.",
      ].join("\n"),
    ]);
  });

  it("uses abstract when recallPreferAbstract=true", async () => {
    const memories = [makeMemory({ abstract: "The abstract text" })];
    const readFn = vi.fn();

    await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
    });

    expect(readFn).not.toHaveBeenCalled();
  });

  it("calls readFn for level=2 when recallPreferAbstract=false", async () => {
    const memories = [makeMemory({ level: 2, abstract: "fallback" })];
    const readFn = vi.fn().mockResolvedValue("Full content from readFn");

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: false,
    });

    expect(readFn).toHaveBeenCalledWith("viking://user/memories/test-1");
    expect(lines[0]).toContain("Full content from readFn");
  });

  it("falls back to abstract when readFn throws", async () => {
    const memories = [makeMemory({ level: 2, abstract: "Fallback abstract" })];
    const readFn = vi.fn().mockRejectedValue(new Error("network error"));

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: false,
    });

    expect(lines[0]).toContain("Fallback abstract");
  });

  it("falls back to abstract when readFn returns empty", async () => {
    const memories = [makeMemory({ level: 2, abstract: "Fallback abstract" })];
    const readFn = vi.fn().mockResolvedValue("");

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: false,
    });

    expect(lines[0]).toContain("Fallback abstract");
  });

  it("keeps individual memory content intact", async () => {
    const longAbstract = "x".repeat(600);
    const memories = [makeMemory({ abstract: longAbstract })];
    const readFn = vi.fn();

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
    });

    expect(lines[0]).toBe(`- [core] ${longAbstract}`);
  });

  it("uses uri as fallback when no abstract", async () => {
    const memories = [makeMemory({ abstract: "", level: 1 })];
    const readFn = vi.fn();

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
    });

    expect(lines[0]).toContain("viking://user/memories/test-1");
  });

  it("defaults category to 'memory'", async () => {
    const memories = [makeMemory({ category: "" })];
    const readFn = vi.fn();

    const lines = await buildMemoryLines(memories, readFn, {
      recallPreferAbstract: true,
    });

    expect(lines[0]).toContain("[memory]");
  });
});

describe("buildMemoryLinesWithBudget", () => {
  it("stops adding before total injected characters exceed the budget", async () => {
    const memories = [
      makeMemory({ abstract: "a".repeat(3000), category: "a" }),
      makeMemory({ abstract: "b".repeat(1500), category: "b" }),
    ];
    const readFn = vi.fn();
    const { lines, estimatedTokens } = await buildMemoryLinesWithBudget(
      memories,
      readFn,
      {
        recallPreferAbstract: true,
        recallMaxInjectedChars: 4000,
      },
    );

    expect(lines).toHaveLength(1);
    expect(lines[0]!.length).toBeLessThanOrEqual(4000);
    expect(estimatedTokens).toBe(estimateTokenCount(lines[0]!));
  });

  it("skips memories that do not fit the remaining character budget", async () => {
    const memories = [
      makeMemory({ abstract: "a".repeat(400), category: "large" }),
      makeMemory({ abstract: "short", category: "small" }),
    ];
    const readFn = vi.fn();

    const { lines } = await buildMemoryLinesWithBudget(
      memories,
      readFn,
      {
        recallPreferAbstract: true,
        recallMaxInjectedChars: 20,
      },
    );

    expect(lines).toHaveLength(1);
    expect(lines[0]).toBe("- [small] short");
  });

  it("returns no lines when no complete memory fits the character budget", async () => {
    const memories = [
      makeMemory({ abstract: "a".repeat(400), category: "large" }),
    ];
    const readFn = vi.fn();

    const { lines, estimatedTokens } = await buildMemoryLinesWithBudget(
      memories,
      readFn,
      {
        recallPreferAbstract: true,
        recallMaxInjectedChars: 20,
      },
    );

    expect(lines).toHaveLength(0);
    expect(estimatedTokens).toBe(0);
  });

  it("returns correct estimatedTokens sum", async () => {
    const memories = [
      makeMemory({ abstract: "short" }),
    ];
    const readFn = vi.fn();

    const { lines, estimatedTokens } = await buildMemoryLinesWithBudget(
      memories,
      readFn,
      {
        recallPreferAbstract: true,
        recallTokenBudget: 2000,
      },
    );

    expect(lines).toHaveLength(1);
    expect(estimatedTokens).toBe(estimateTokenCount(lines[0]!));
  });

  it("handles empty memories array", async () => {
    const readFn = vi.fn();
    const { lines, estimatedTokens } = await buildMemoryLinesWithBudget(
      [],
      readFn,
      {
        recallPreferAbstract: true,
        recallTokenBudget: 2000,
      },
    );

    expect(lines).toHaveLength(0);
    expect(estimatedTokens).toBe(0);
  });
});

describe("buildAutoRecallContext trace", () => {
  function makeCfg(overrides: Record<string, unknown> = {}) {
    return memoryOpenVikingConfigSchema.parse({
      autoRecall: true,
      recallPreferAbstract: true,
      ...overrides,
    });
  }

  it("records auto-recall trace without changing the generated context block", async () => {
    const cfg = makeCfg({ recallTargetTypes: ["user"] });
    const memory = makeMemory({
      uri: "viking://user/memories/rust-pref",
      category: "preferences",
      abstract: "User prefers Rust for backend tasks.",
      score: 0.91,
    });
    const makeClient = () => ({
      healthCheck: vi.fn().mockResolvedValue(undefined),
      find: vi.fn().mockResolvedValue({ memories: [memory], total: 1 }),
      read: vi.fn().mockResolvedValue("unused"),
    });
    const logger = { info: vi.fn(), warn: vi.fn() };

    const withoutTrace = await buildAutoRecallContext({
      cfg,
      client: makeClient() as any,
      agentId: "agent-1",
      queryText: "what backend language should we use?",
      logger,
    });
    const traces = new RecallTraceMemoryStore(10);
    const withTrace = await buildAutoRecallContext({
      cfg,
      client: makeClient() as any,
      agentId: "agent-1",
      queryText: "what backend language should we use?",
      logger,
      traceRecorder: traces,
      sessionId: "session-1",
      ovSessionId: "ov-1",
      queryTruncated: false,
    });

    expect(withTrace.block).toBe(withoutTrace.block);
    const recorded = traces.query({ turn: "latest", sessionId: "session-1", limit: 10 }).entries[0]!;
    expect(recorded.source).toBe("auto_recall");
    expect(recorded.operationType).toBe("semantic_find");
    expect(recorded.resourceTypes).toEqual(["user"]);
    expect(recorded.trigger).toMatchObject({
      query: "what backend language should we use?",
      queryTruncated: false,
    });
    expect(recorded.searches).toHaveLength(1);
    expect(recorded.searches[0]).toMatchObject({
      resourceType: "user",
      total: 1,
    });
    expect(recorded.searches[0]!.targetUriInput).toBeUndefined();
    expect(recorded.searches[0]!.results[0]).toMatchObject({
      uri: "viking://user/memories/rust-pref",
      resourceType: "user",
      resultType: "memory",
    });
    expect(recorded.selected[0]).toMatchObject({
      uri: "viking://user/memories/rust-pref",
      injected: true,
    });
    expect(recorded.stats.injectedCount).toBe(1);
  });

  it("defaults auto-recall to backward-compatible user and agent memory recall", async () => {
    const cfg = makeCfg();
    const userMemory = makeMemory({
      uri: "viking://user/memories/project-docs",
      category: "memory",
      abstract: "Project documentation preference.",
      score: 0.9,
    });
    const client = {
      healthCheck: vi.fn().mockResolvedValue(undefined),
      find: vi.fn(async (_query: string, options: { contextType?: string }) => ({
        memories: options.contextType === "memory" ? [userMemory] : [],
        resources: [],
        total: options.contextType === "memory" ? 1 : 0,
      })),
      read: vi.fn().mockResolvedValue("unused"),
    };
    const traces = new RecallTraceMemoryStore(10);

    await buildAutoRecallContext({
      cfg,
      client: client as any,
      agentId: "agent-1",
      queryText: "where are the project docs?",
      logger: { info: vi.fn(), warn: vi.fn() },
      traceRecorder: traces,
      sessionId: "session-resource-only",
      ovSessionId: "ov-1",
    });

    expect(client.find).toHaveBeenCalledTimes(1);
    expect(client.find.mock.calls[0]?.[1]).toMatchObject({
      contextType: "memory",
      targetUri: undefined,
    });
    const recorded = traces.query({ turn: "latest", sessionId: "session-resource-only", limit: 10 }).entries[0]!;
    expect(recorded.resourceTypes).toEqual(["user", "agent"]);
    expect(recorded.searches.map((search) => search.resourceType)).toEqual(["user"]);
  });

  it("records search errors while still injecting successful recall hits", async () => {
    const cfg = makeCfg({ recallTargetTypes: ["resource", "user"] });
    const client = {
      healthCheck: vi.fn().mockResolvedValue(undefined),
      find: vi.fn()
        .mockRejectedValueOnce(new Error("resource search failed"))
        .mockResolvedValueOnce({
          memories: [makeMemory({
            uri: "viking://user/memories/backend-pref",
            abstract: "Agent recommends TypeScript for this service.",
            score: 0.88,
          })],
          total: 1,
        }),
      read: vi.fn().mockResolvedValue("unused"),
    };
    const logger = { info: vi.fn(), warn: vi.fn() };
    const traces = new RecallTraceMemoryStore(10);

    const result = await buildAutoRecallContext({
      cfg,
      client: client as any,
      agentId: "agent-1",
      queryText: "which language should this service use?",
      logger,
      traceRecorder: traces,
      sessionId: "session-err",
    });

    expect(result.block).toContain("Agent recommends TypeScript for this service.");
    const recorded = traces.query({ turn: "latest", sessionId: "session-err", limit: 10 }).entries[0]!;
    expect(recorded.searches).toHaveLength(2);
    expect(recorded.searches[0]).toMatchObject({
      resourceType: "resource",
      error: "Error: resource search failed",
    });
    expect(recorded.searches[1]).toMatchObject({
      resourceType: "user",
      total: 1,
    });
    expect(recorded.selected[0]).toMatchObject({ injected: true });
    expect(logger.warn).toHaveBeenCalledWith(expect.stringContaining("auto-recall search failed"));
  });
});
