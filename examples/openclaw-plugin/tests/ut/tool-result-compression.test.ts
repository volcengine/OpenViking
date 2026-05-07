import { describe, it, expect, vi } from "vitest";
import { compressToolResults, prePersistOversizedResults } from "../../tool-result-compression.js";
import { mkdir, writeFile, readFile } from "node:fs/promises";

vi.mock("node:fs/promises", () => ({
  mkdir: vi.fn().mockResolvedValue(undefined),
  writeFile: vi.fn().mockResolvedValue(undefined),
  readFile: vi.fn().mockResolvedValue(""),
}));

afterEach(() => {
  vi.restoreAllMocks();
  (mkdir as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
  (writeFile as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
  (readFile as ReturnType<typeof vi.fn>).mockResolvedValue("");
});

function makeToolResult(content: string, toolName = "test", toolCallId?: string): { role: string; content: string; toolName: string; toolCallId?: string } {
  return { role: "toolResult", content, toolName, ...(toolCallId ? { toolCallId } : {}) };
}

function makeAssistantMsg(text: string): { role: string; content: string } {
  return { role: "assistant", content: text };
}

function makeCfg(overrides: Record<string, unknown> = {}): {
  toolResultCompression: boolean;
  toolResultMaxChars: number;
  toolResultAggregateBudgetChars: number;
  toolResultPreviewChars: number;
  toolResultStorageDir?: string;
  sessionId?: string;
} {
  return {
    toolResultCompression: true,
    toolResultMaxChars: 20_000,
    toolResultAggregateBudgetChars: 100_000,
    toolResultPreviewChars: 2_000,
    toolResultStorageDir: "/tmp/test-tool-results",
    sessionId: "test-session-001",
    ...overrides,
  };
}

describe("compressToolResults", () => {
  it("passes through messages with no tool results", async () => {
    const messages = [{ role: "user", content: "hello" }, makeAssistantMsg("hi")];
    const { messages: result, stats } = await compressToolResults(messages, makeCfg());
    expect(result).toEqual(messages);
    expect(stats.compressedCount).toBe(0);
  });

  it("passes through small tool results unchanged", async () => {
    const messages = [makeToolResult("small output")];
    const { messages: result, stats } = await compressToolResults(messages, makeCfg());
    expect(result[0]).toEqual(messages[0]);
    expect(stats.compressedCount).toBe(0);
  });

  it("persists oversized tool result and replaces with preview", async () => {
    const bigContent = "x".repeat(30_000);
    const messages = [makeToolResult(bigContent, "bash", "call-123")];
    const { messages: result, stats } = await compressToolResults(messages, makeCfg({ toolResultMaxChars: 10_000 }));
    expect(stats.compressedCount).toBe(1);
    expect(stats.persistedFiles.length).toBe(1);
    const text = (result[0] as { content: string }).content as string;
    expect(text).toContain("<persisted-output>");
    expect(text).toContain("Full output saved to:");
    expect(text).toContain("</persisted-output>");
    expect(writeFile).toHaveBeenCalled();
  });

  it("preserves non-tool-result messages", async () => {
    const bigContent = "x".repeat(30_000);
    const messages = [
      { role: "user", content: "question" },
      makeAssistantMsg("let me check"),
      makeToolResult(bigContent),
      makeAssistantMsg("based on the result"),
    ];
    const { messages: result } = await compressToolResults(messages, makeCfg({ toolResultMaxChars: 10_000 }));
    expect(result[0]).toEqual(messages[0]);
    expect(result[1]).toEqual(messages[1]);
    expect(result[3]).toEqual(messages[3]);
  });

  it("applies head+tail preview for error content", async () => {
    const errorContent = "x".repeat(15_000) + "\n\nError: something went wrong\nStack trace:\n  at line 42";
    const messages = [makeToolResult(errorContent)];
    const { messages: result } = await compressToolResults(messages, makeCfg({ toolResultMaxChars: 10_000 }));
    const text = (result[0] as { content: string }).content as string;
    expect(text).toContain("Error");
    expect(text).toContain("<persisted-output>");
  });

  it("respects toolResultCompression=false to skip compression", async () => {
    const bigContent = "x".repeat(100_000);
    const messages = [makeToolResult(bigContent)];
    const { messages: result, stats } = await compressToolResults(messages, makeCfg({ toolResultCompression: false }));
    expect(result[0]).toEqual(messages[0]);
    expect(stats.compressedCount).toBe(0);
    expect(stats.persistedFiles.length).toBe(0);
  });

  it("falls back to truncation when disk write fails", async () => {
    (writeFile as ReturnType<typeof vi.fn>).mockRejectedValueOnce(Object.assign(new Error("no space"), { code: "ENOSPC" }));
    const bigContent = "y".repeat(30_000);
    const messages = [makeToolResult(bigContent)];
    const { messages: result, stats } = await compressToolResults(messages, makeCfg({ toolResultMaxChars: 10_000 }));
    expect(stats.compressedCount).toBe(1);
    expect(stats.persistedFiles.length).toBe(0);
    const text = (result[0] as { content: string }).content as string;
    expect(text).toContain("not recoverable");
  });

  it("skips write when file already exists with same content (EEXIST)", async () => {
    const bigContent = "z".repeat(30_000);
    (writeFile as ReturnType<typeof vi.fn>).mockRejectedValueOnce(Object.assign(new Error("exists"), { code: "EEXIST" }));
    (readFile as ReturnType<typeof vi.fn>).mockResolvedValueOnce(bigContent);
    const messages = [makeToolResult(bigContent, "test", "existing-id")];
    const { messages: result, stats } = await compressToolResults(messages, makeCfg({ toolResultMaxChars: 10_000 }));
    expect(stats.compressedCount).toBe(1);
    expect(stats.persistedFiles.length).toBe(1);
    const text = (result[0] as { content: string }).content as string;
    expect(text).toContain("Full output saved to:");
  });
});

describe("aggregate budget (per assistant turn)", () => {
  it("triggers when tool results in same turn exceed budget", async () => {
    const messages = [
      makeToolResult("a".repeat(30_000)),
      makeToolResult("b".repeat(30_000)),
      makeToolResult("c".repeat(30_000)),
    ];
    const { stats } = await compressToolResults(messages, makeCfg({
      toolResultMaxChars: 100_000,
      toolResultAggregateBudgetChars: 40_000,
    }));
    expect(stats.aggregateBudgetTriggered).toBe(true);
    expect(stats.compressedCount).toBeGreaterThan(0);
  });

  it("applies per turn, not globally", async () => {
    const messages = [
      makeToolResult("a".repeat(20_000)),
      makeToolResult("b".repeat(20_000)),
      makeToolResult("c".repeat(20_000)),
      makeAssistantMsg("let me do more"),
      makeToolResult("d".repeat(20_000)),
      makeToolResult("e".repeat(20_000)),
      makeToolResult("f".repeat(20_000)),
    ];
    const { stats } = await compressToolResults(messages, makeCfg({
      toolResultMaxChars: 100_000,
      toolResultAggregateBudgetChars: 80_000,
    }));
    expect(stats.aggregateBudgetTriggered).toBe(false);
    expect(stats.compressedCount).toBe(0);
  });

  it("only triggers for the turn that exceeds it", async () => {
    const messages = [
      makeToolResult("a".repeat(10_000)),
      makeToolResult("b".repeat(10_000)),
      makeAssistantMsg("next step"),
      makeToolResult("d".repeat(30_000)),
      makeToolResult("e".repeat(30_000)),
      makeToolResult("f".repeat(30_000)),
    ];
    const { stats } = await compressToolResults(messages, makeCfg({
      toolResultMaxChars: 100_000,
      toolResultAggregateBudgetChars: 50_000,
    }));
    expect(stats.aggregateBudgetTriggered).toBe(true);
  });

  it("persists original content before aggregate truncation so output is recoverable", async () => {
    // 6 results × 19K = 114K > 100K aggregate budget, but each is under 20K maxChars.
    // This is the exact data-loss scenario from the review: Phase 1 won't trigger,
    // Phase 2 must persist before truncating.
    const messages = [
      makeToolResult("a".repeat(19_000), "bash", "r1"),
      makeToolResult("b".repeat(19_000), "bash", "r2"),
      makeToolResult("c".repeat(19_000), "bash", "r3"),
      makeToolResult("d".repeat(19_000), "bash", "r4"),
      makeToolResult("e".repeat(19_000), "bash", "r5"),
      makeToolResult("f".repeat(19_000), "bash", "r6"),
    ];
    const { stats, messages: result } = await compressToolResults(messages, makeCfg({
      toolResultMaxChars: 20_000,
      toolResultAggregateBudgetChars: 100_000,
    }));
    expect(stats.aggregateBudgetTriggered).toBe(true);
    expect(stats.compressedCount).toBeGreaterThan(0);
    // The key assertion: files were persisted so the full output is recoverable.
    expect(stats.persistedFiles.length).toBeGreaterThan(0);
    // And the truncated messages should reference the persisted file paths.
    let foundRecoverablePath = false;
    for (const msg of result) {
      const text = typeof msg.content === "string" ? msg.content : "";
      if (text.includes("full output saved to:")) foundRecoverablePath = true;
    }
    expect(foundRecoverablePath).toBe(true);
  });

  it("marks aggregate-truncated output as not recoverable when disk fails", async () => {
    (writeFile as ReturnType<typeof vi.fn>).mockRejectedValue(Object.assign(new Error("no space"), { code: "ENOSPC" }));
    const messages = [
      makeToolResult("a".repeat(19_000), "bash", "r1"),
      makeToolResult("b".repeat(19_000), "bash", "r2"),
      makeToolResult("c".repeat(19_000), "bash", "r3"),
      makeToolResult("d".repeat(19_000), "bash", "r4"),
      makeToolResult("e".repeat(19_000), "bash", "r5"),
      makeToolResult("f".repeat(19_000), "bash", "r6"),
    ];
    const { stats, messages: result } = await compressToolResults(messages, makeCfg({
      toolResultMaxChars: 20_000,
      toolResultAggregateBudgetChars: 100_000,
    }));
    expect(stats.aggregateBudgetTriggered).toBe(true);
    expect(stats.persistedFiles.length).toBe(0);
    let foundNotRecoverable = false;
    for (const msg of result) {
      const text = typeof msg.content === "string" ? msg.content : "";
      if (text.includes("not recoverable")) foundNotRecoverable = true;
    }
    expect(foundNotRecoverable).toBe(true);
  });
});

describe("prePersistOversizedResults", () => {
  it("persists oversized results before any trimming", async () => {
    const messages = [
      makeToolResult("small"),
      makeToolResult("x".repeat(30_000), "bash", "big-1"),
      makeToolResult("x".repeat(25_000), "bash", "big-2"),
    ];
    const files = await prePersistOversizedResults(messages, makeCfg({ toolResultMaxChars: 20_000 }));
    expect(files.length).toBe(2);
  });

  it("returns empty when no oversized results", async () => {
    const messages = [makeToolResult("small")];
    const files = await prePersistOversizedResults(messages, makeCfg());
    expect(files).toEqual([]);
  });

  it("skips when disabled", async () => {
    const messages = [makeToolResult("x".repeat(30_000))];
    const files = await prePersistOversizedResults(messages, makeCfg({ toolResultCompression: false }));
    expect(files).toEqual([]);
  });
});
