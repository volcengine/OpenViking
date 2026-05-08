import { describe, expect, it, vi } from "vitest";
import type { RecallHit } from "../ov-client.js";
import { formatRecallBlock } from "../recall/format.js";
import { stripInjectedBlocks } from "../capture/sanitize.js";

const BIG_BUDGET = 100_000;

describe("formatRecallBlock — block shape", () => {
  it("returns null block + zero counts for an empty input", async () => {
    const out = await formatRecallBlock([], {
      tokenBudget: BIG_BUDGET,
      maxContentChars: 500,
      preferAbstract: true,
    });
    expect(out.block).toBeNull();
    expect(out.contentCount).toBe(0);
    expect(out.hintCount).toBe(0);
    expect(out.budgetUsed).toBe(0);
  });

  it("opens with <openviking-context>, uses the verbatim header line, closes with </openviking-context>", async () => {
    const items: RecallHit[] = [
      { uri: "viking://m/1", type: "memory", score: 0.8, abstract: "first abstract" },
    ];
    const { block } = await formatRecallBlock(items, {
      tokenBudget: BIG_BUDGET,
      maxContentChars: 500,
      preferAbstract: true,
    });
    expect(block).not.toBeNull();
    const lines = block!.split("\n");
    expect(lines[0]).toBe("<openviking-context>");
    expect(lines[1]).toBe("Relevant context from OpenViking. Use the read MCP tool to expand URIs.");
    expect(lines.at(-1)).toBe("</openviking-context>");
  });

  it("renders one line per item as `- [<type> <score>%] <content>`", async () => {
    const items: RecallHit[] = [
      { uri: "viking://m/a", type: "memory", score: 0.8, abstract: "AAA" },
      { uri: "viking://s/b", type: "skill", score: 0.45, abstract: "BBB" },
    ];
    const { block } = await formatRecallBlock(items, {
      tokenBudget: BIG_BUDGET,
      maxContentChars: 500,
      preferAbstract: true,
    });
    const itemLines = block!.split("\n").slice(2, -1);
    expect(itemLines).toEqual([
      "- [memory 80%] AAA",
      "- [skill 45%] BBB",
    ]);
  });

  it("rounds the score percentage to a whole number with no decimals", async () => {
    const items: RecallHit[] = [
      { uri: "viking://m/x", type: "memory", score: 0.345, abstract: "x" },
      { uri: "viking://m/y", type: "memory", score: 1.0, abstract: "y" },
      { uri: "viking://m/z", type: "memory", score: 0, abstract: "z" },
    ];
    const { block } = await formatRecallBlock(items, {
      tokenBudget: BIG_BUDGET,
      maxContentChars: 500,
      preferAbstract: true,
    });
    expect(block).toContain("[memory 35%]");
    expect(block).toContain("[memory 100%]");
    expect(block).toContain("[memory 0%]");
    expect(block).not.toMatch(/\d\.\d+%/);
  });

  it("uses a fallback type label when the hit has no `type` field", async () => {
    const { block } = await formatRecallBlock(
      [{ uri: "viking://x", score: 0.5, abstract: "y" }],
      { tokenBudget: BIG_BUDGET, maxContentChars: 500, preferAbstract: true },
    );
    expect(block).toContain("[item 50%]");
  });

  it("emits a block that capture/sanitize.ts can strip cleanly", async () => {
    const items: RecallHit[] = [
      { uri: "viking://m/a", type: "memory", score: 0.9, abstract: "x" },
    ];
    const { block } = await formatRecallBlock(items, {
      tokenBudget: BIG_BUDGET,
      maxContentChars: 500,
      preferAbstract: true,
    });
    const surrounded = `before turn N user message\n${block}\nafter turn N`;
    const stripped = stripInjectedBlocks(surrounded);
    expect(stripped).not.toContain("openviking-context");
    expect(stripped).not.toContain("Relevant context from OpenViking");
    expect(stripped).toContain("before turn N");
    expect(stripped).toContain("after turn N");
  });
});

describe("formatRecallBlock — token budget", () => {
  it("degrades over-budget items to URI-only hints", async () => {
    const items: RecallHit[] = [
      // ~6 tokens of content (chars/4)
      { uri: "viking://m/a", type: "memory", score: 0.9, abstract: "alpha".repeat(2) },
      // budget exhausted by item a, so b degrades
      { uri: "viking://m/b", type: "memory", score: 0.7, abstract: "this is a much longer abstract that ought to be far over the remaining budget for sure" },
    ];
    const { block, contentCount, hintCount } = await formatRecallBlock(items, {
      tokenBudget: 12, // small enough that item b can't fit content but a can
      maxContentChars: 500,
      preferAbstract: true,
    });
    const itemLines = block!.split("\n").slice(2, -1);
    // item a → content, item b → URI hint
    expect(itemLines[0]).toMatch(/^- \[memory 90%\] alphaalpha$/);
    expect(itemLines[1]).toBe("- [memory 70%] viking://m/b");
    expect(contentCount).toBe(1);
    expect(hintCount).toBe(1);
  });

  it("always renders the FIRST item with content even if it alone exceeds the budget", async () => {
    const items: RecallHit[] = [
      { uri: "viking://m/big", type: "memory", score: 0.95, abstract: "X".repeat(2000) },
    ];
    const { block, contentCount, hintCount } = await formatRecallBlock(items, {
      tokenBudget: 5,
      maxContentChars: 500,
      preferAbstract: true,
    });
    expect(contentCount).toBe(1);
    expect(hintCount).toBe(0);
    const itemLine = block!.split("\n").slice(2, -1)[0]!;
    expect(itemLine.startsWith("- [memory 95%] X")).toBe(true);
  });

  it("budgetUsed reports tokens consumed by content lines (excluding hints)", async () => {
    const items: RecallHit[] = [
      { uri: "viking://m/a", type: "memory", score: 0.9, abstract: "abcd" },
      { uri: "viking://m/b", type: "memory", score: 0.6, abstract: "very long content that should overflow" },
    ];
    const { budgetUsed } = await formatRecallBlock(items, {
      tokenBudget: 8,
      maxContentChars: 500,
      preferAbstract: true,
    });
    // content line for a is short but non-zero; b is hinted so doesn't count
    expect(budgetUsed).toBeGreaterThan(0);
    expect(budgetUsed).toBeLessThanOrEqual(8);
  });

  it("treats tokenBudget=0 as URI-hints-everywhere from item one", async () => {
    const items: RecallHit[] = [
      { uri: "viking://m/a", type: "memory", score: 0.9, abstract: "any" },
      { uri: "viking://m/b", type: "memory", score: 0.7, abstract: "any" },
    ];
    const { block, contentCount, hintCount } = await formatRecallBlock(items, {
      tokenBudget: 0,
      maxContentChars: 500,
      preferAbstract: true,
    });
    expect(contentCount).toBe(0);
    expect(hintCount).toBe(2);
    const itemLines = block!.split("\n").slice(2, -1);
    expect(itemLines).toEqual([
      "- [memory 90%] viking://m/a",
      "- [memory 70%] viking://m/b",
    ]);
  });
});

describe("formatRecallBlock — content resolution", () => {
  it("preferAbstract=true uses the abstract even when level=2 and a fetchContent is provided", async () => {
    const fetchContent = vi.fn(async () => "FETCHED-FULL-BODY");
    const items: RecallHit[] = [
      { uri: "viking://m/a", type: "memory", score: 0.8, abstract: "ABS", level: 2 },
    ];
    const { block } = await formatRecallBlock(items, {
      tokenBudget: BIG_BUDGET,
      maxContentChars: 500,
      preferAbstract: true,
      fetchContent,
    });
    expect(block).toContain("[memory 80%] ABS");
    expect(fetchContent).not.toHaveBeenCalled();
  });

  it("preferAbstract=false + level=2 calls fetchContent and uses its output", async () => {
    const fetchContent = vi.fn(async () => "FETCHED-FULL-BODY");
    const items: RecallHit[] = [
      { uri: "viking://m/a", type: "memory", score: 0.8, abstract: "ABS", level: 2 },
    ];
    const { block } = await formatRecallBlock(items, {
      tokenBudget: BIG_BUDGET,
      maxContentChars: 500,
      preferAbstract: false,
      fetchContent,
    });
    expect(block).toContain("[memory 80%] FETCHED-FULL-BODY");
    expect(fetchContent).toHaveBeenCalledWith("viking://m/a");
  });

  it("fetchContent returning null falls back to abstract", async () => {
    const fetchContent = vi.fn(async () => null);
    const { block } = await formatRecallBlock(
      [{ uri: "viking://m/a", type: "memory", score: 0.8, abstract: "ABS", level: 2 }],
      { tokenBudget: BIG_BUDGET, maxContentChars: 500, preferAbstract: false, fetchContent },
    );
    expect(block).toContain("[memory 80%] ABS");
  });

  it("fetchContent throwing falls back to abstract (single dead resource doesn't break the block)", async () => {
    const fetchContent = vi.fn(async () => { throw new Error("boom"); });
    const { block } = await formatRecallBlock(
      [{ uri: "viking://m/a", type: "memory", score: 0.8, abstract: "ABS", level: 2 }],
      { tokenBudget: BIG_BUDGET, maxContentChars: 500, preferAbstract: false, fetchContent },
    );
    expect(block).toContain("[memory 80%] ABS");
  });

  it("falls back to URI when neither abstract nor fetched content is available", async () => {
    const { block } = await formatRecallBlock(
      [{ uri: "viking://m/empty", type: "memory", score: 0.5 }],
      { tokenBudget: BIG_BUDGET, maxContentChars: 500, preferAbstract: true },
    );
    expect(block).toContain("[memory 50%] viking://m/empty");
  });

  it("non-level-2 items always use the abstract regardless of preferAbstract", async () => {
    const fetchContent = vi.fn();
    const { block } = await formatRecallBlock(
      [{ uri: "viking://m/a", type: "memory", score: 0.5, abstract: "ABS" /* no level */ }],
      { tokenBudget: BIG_BUDGET, maxContentChars: 500, preferAbstract: false, fetchContent },
    );
    expect(block).toContain("[memory 50%] ABS");
    expect(fetchContent).not.toHaveBeenCalled();
  });
});

describe("formatRecallBlock — recallMaxContentChars", () => {
  it("truncates content over maxContentChars and appends ...", async () => {
    const longAbs = "x".repeat(700);
    const { block } = await formatRecallBlock(
      [{ uri: "viking://m/long", type: "memory", score: 0.5, abstract: longAbs }],
      { tokenBudget: BIG_BUDGET, maxContentChars: 50, preferAbstract: true },
    );
    const itemLine = block!.split("\n").slice(2, -1)[0]!;
    expect(itemLine).toMatch(/^- \[memory 50%\] x{50}\.\.\.$/);
  });

  it("does not truncate content at exactly maxContentChars", async () => {
    const exact = "x".repeat(50);
    const { block } = await formatRecallBlock(
      [{ uri: "viking://m/exact", type: "memory", score: 0.5, abstract: exact }],
      { tokenBudget: BIG_BUDGET, maxContentChars: 50, preferAbstract: true },
    );
    const itemLine = block!.split("\n").slice(2, -1)[0]!;
    expect(itemLine).toBe(`- [memory 50%] ${exact}`);
  });
});
