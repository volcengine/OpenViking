import { describe, it, expect, vi } from "vitest";
import { compressToolResults } from "../../tool-result-compression.js";
import { mkdir, writeFile } from "node:fs/promises";

vi.mock("node:fs/promises", () => ({
  mkdir: vi.fn().mockResolvedValue(undefined),
  writeFile: vi.fn().mockResolvedValue(undefined),
}));

function makeToolResult(content: string, toolName = "test", toolCallId?: string): { role: string; content: string; toolName: string; toolCallId?: string } {
  return { role: "toolResult", content, toolName, ...(toolCallId ? { toolCallId } : {}) };
}

function makeUserMsg(text: string): { role: string; content: string } {
  return { role: "user", content: text };
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
    const messages = [makeUserMsg("hello"), makeAssistantMsg("hi")];
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

  it("persists oversized tool result to disk and replaces with preview", async () => {
    const bigContent = "x".repeat(30_000);
    const messages = [makeToolResult(bigContent, "bash", "call-123")];
    const { messages: result, stats } = await compressToolResults(messages, makeCfg({ toolResultMaxChars: 10_000 }));
    expect(stats.compressedCount).toBe(1);
    expect(stats.persistedFiles.length).toBe(1);
    const text = (result[0] as { content: string }).content as string;
    expect(text).toContain("<persisted-output>");
    expect(text).toContain("Full output saved to:");
    expect(text).toContain("</persisted-output>");
    expect(text.length).toBeLessThan(30_000);
    expect(writeFile).toHaveBeenCalled();
  });

  it("preserves non-tool-result messages", async () => {
    const bigContent = "x".repeat(30_000);
    const messages = [
      makeUserMsg("question"),
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

  it("triggers aggregate budget when tool results in same turn exceed budget", async () => {
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

  it("applies aggregate budget per assistant turn, not globally", async () => {
    // Turn 1: 3 results × 20K = 60K (under budget)
    // Turn 2: 3 results × 20K = 60K (under budget)
    // Global total = 120K which exceeds budget, but each turn is within budget.
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

  it("triggers aggregate budget only for the turn that exceeds it", async () => {
    // Turn 1: 2 results × 10K = 20K (under budget)
    // Turn 2: 3 results × 30K = 90K (over budget)
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
    expect(text).toContain("disk persistence failed");
  });

  it("skips write when file already exists (EEXIST)", async () => {
    (writeFile as ReturnType<typeof vi.fn>).mockRejectedValueOnce(Object.assign(new Error("exists"), { code: "EEXIST" }));
    const bigContent = "z".repeat(30_000);
    const messages = [makeToolResult(bigContent, "test", "existing-id")];
    const { messages: result, stats } = await compressToolResults(messages, makeCfg({ toolResultMaxChars: 10_000 }));
    expect(stats.compressedCount).toBe(1);
    expect(stats.persistedFiles.length).toBe(1);
    const text = (result[0] as { content: string }).content as string;
    expect(text).toContain("Full output saved to:");
  });
});
