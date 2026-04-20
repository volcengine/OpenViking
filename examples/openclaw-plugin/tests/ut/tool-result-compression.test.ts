import { describe, it, expect } from "vitest";
import { compressToolResults } from "../../tool-result-compression.js";

function makeToolResult(content: string, toolName = "test"): { role: string; content: string; toolName: string } {
  return { role: "toolResult", content, toolName };
}

function makeToolResultArray(content: Array<{ type: string; text: string }>): { role: string; content: unknown[]; toolName: string } {
  return { role: "toolResult", content, toolName: "test" };
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
} {
  return {
    toolResultCompression: true,
    toolResultMaxChars: 20_000,
    toolResultAggregateBudgetChars: 100_000,
    toolResultPreviewChars: 2_000,
    ...overrides,
  };
}

describe("compressToolResults", () => {
  it("passes through messages with no tool results", () => {
    const messages = [makeUserMsg("hello"), makeAssistantMsg("hi")];
    const { messages: result, stats } = compressToolResults(messages, makeCfg());
    expect(result).toEqual(messages);
    expect(stats.compressedCount).toBe(0);
  });

  it("passes through small tool results unchanged", () => {
    const messages = [makeToolResult("small output")];
    const { messages: result, stats } = compressToolResults(messages, makeCfg());
    expect(result[0]).toEqual(messages[0]);
    expect(stats.compressedCount).toBe(0);
  });

  it("truncates oversized tool result", () => {
    const bigContent = "x".repeat(30_000);
    const messages = [makeToolResult(bigContent)];
    const { messages: result, stats } = compressToolResults(messages, makeCfg({ toolResultMaxChars: 10_000 }));
    expect(stats.compressedCount).toBe(1);
    const text = (result[0] as { content: string }).content;
    expect(text.length).toBeLessThan(30_000);
    expect(text).toContain("truncated");
  });

  it("preserves non-tool-result messages", () => {
    const bigContent = "x".repeat(30_000);
    const messages = [
      makeUserMsg("question"),
      makeAssistantMsg("let me check"),
      makeToolResult(bigContent),
      makeAssistantMsg("based on the result"),
    ];
    const { messages: result } = compressToolResults(messages, makeCfg({ toolResultMaxChars: 10_000 }));
    expect(result[0]).toEqual(messages[0]);
    expect(result[1]).toEqual(messages[1]);
    expect(result[3]).toEqual(messages[3]);
  });

  it("handles array content tool results", () => {
    const bigText = "y".repeat(30_000);
    const messages = [
      makeToolResultArray([{ type: "text", text: bigText }]),
    ];
    const { messages: result, stats } = compressToolResults(messages, makeCfg({ toolResultMaxChars: 10_000 }));
    expect(stats.compressedCount).toBe(1);
    const content = (result[0] as { content: Array<{ type: string; text: string }> }).content;
    expect(content[0].text.length).toBeLessThan(bigText.length);
  });

  it("applies head+tail truncation for error content", () => {
    const errorContent = "x".repeat(15_000) + "\n\nError: something went wrong\nStack trace:\n  at line 42";
    const messages = [makeToolResult(errorContent)];
    const { messages: result } = compressToolResults(messages, makeCfg({ toolResultMaxChars: 10_000 }));
    const text = (result[0] as { content: string }).content;
    expect(text).toContain("Error");
    expect(text).toContain("truncated");
  });

  it("triggers aggregate budget when total tool results exceed budget", () => {
    const messages = [
      makeToolResult("a".repeat(30_000)),
      makeToolResult("b".repeat(30_000)),
      makeToolResult("c".repeat(30_000)),
    ];
    const { messages: _result, stats } = compressToolResults(messages, makeCfg({
      toolResultMaxChars: 100_000,
      toolResultAggregateBudgetChars: 40_000,
    }));
    expect(stats.aggregateBudgetTriggered).toBe(true);
    expect(stats.compressedCount).toBeGreaterThan(0);
  });

  it("respects toolResultCompression=false to skip compression", () => {
    const bigContent = "x".repeat(100_000);
    const messages = [makeToolResult(bigContent)];
    const { messages: result, stats } = compressToolResults(messages, makeCfg({ toolResultCompression: false }));
    expect(result[0]).toEqual(messages[0]);
    expect(stats.compressedCount).toBe(0);
  });

  it("uses default config values", () => {
    const bigContent = "x".repeat(25_000);
    const messages = [makeToolResult(bigContent)];
    const { stats } = compressToolResults(messages, makeCfg());
    expect(stats.compressedCount).toBe(1);
  });
});
