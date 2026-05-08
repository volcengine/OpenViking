import { describe, expect, it } from "vitest";
import {
  INJECTED_BLOCK_PATTERNS,
  sanitize,
  stripInjectedBlocks,
} from "../capture/sanitize.js";

describe("stripInjectedBlocks — marker removal", () => {
  it("removes <openviking-context> blocks", () => {
    const input = "before <openviking-context>recall hits go here</openviking-context> after";
    expect(stripInjectedBlocks(input)).toBe("before  after");
  });

  it("removes <relevant-memories> blocks", () => {
    const input = "x <relevant-memories>\n- m1\n- m2\n</relevant-memories> y";
    expect(stripInjectedBlocks(input)).toBe("x  y");
  });

  it("removes <system-reminder> blocks", () => {
    const input = "<system-reminder>internal note</system-reminder>only-this-survives";
    expect(stripInjectedBlocks(input)).toBe("only-this-survives");
  });

  it("removes <copilot-context> blocks (the Copilot-side analogue)", () => {
    const input = "ok <copilot-context>injected</copilot-context> done";
    expect(stripInjectedBlocks(input)).toBe("ok  done");
  });

  it("removes [Subagent Context] lines but keeps surrounding lines", () => {
    const input = "before line\n[Subagent Context] some metadata\nafter line";
    expect(stripInjectedBlocks(input)).toBe("before line\n\nafter line");
  });

  it("removes NUL characters", () => {
    expect(stripInjectedBlocks("a\x00b\x00c")).toBe("abc");
  });

  it("removes multiple instances of the same marker on one line", () => {
    const input =
      "<openviking-context>a</openviking-context> mid <openviking-context>b</openviking-context>";
    expect(stripInjectedBlocks(input)).toBe(" mid ");
  });

  it("removes multi-line block contents", () => {
    const input = `keep me
<openviking-context>
  line one
  line two
</openviking-context>
keep me too`;
    expect(stripInjectedBlocks(input)).toBe(`keep me

keep me too`);
  });

  it("removes a mix of all marker kinds in one input", () => {
    const input = `prologue
<system-reminder>x</system-reminder>
<openviking-context>recall</openviking-context>
<copilot-context>cp</copilot-context>
<relevant-memories>rm</relevant-memories>
[Subagent Context] meta
\x00
epilogue`;
    const out = stripInjectedBlocks(input);
    expect(out).not.toMatch(/system-reminder|openviking-context|copilot-context|relevant-memories|Subagent Context/);
    expect(out).toContain("prologue");
    expect(out).toContain("epilogue");
    expect(out).not.toContain("\x00");
  });
});

describe("stripInjectedBlocks — whitespace and shape preservation", () => {
  it("preserves newlines outside the stripped blocks", () => {
    const input = "line 1\nline 2\n<openviking-context>x</openviking-context>\nline 3";
    expect(stripInjectedBlocks(input)).toBe("line 1\nline 2\n\nline 3");
  });

  it("preserves code fences and their indentation", () => {
    const input = "```ts\n  const x = 1;\n```\n<system-reminder>noise</system-reminder>";
    expect(stripInjectedBlocks(input)).toBe("```ts\n  const x = 1;\n```\n");
  });

  it("returns the empty string unchanged", () => {
    expect(stripInjectedBlocks("")).toBe("");
  });

  it("returns clean text unchanged (no false-positive trims)", () => {
    const clean = "Hello.\n\nThis text has no markers.";
    expect(stripInjectedBlocks(clean)).toBe(clean);
  });
});

describe("stripInjectedBlocks — idempotency", () => {
  it("is idempotent: strip(strip(x)) === strip(x)", () => {
    const input = `<openviking-context>a</openviking-context>
keep
<system-reminder>b</system-reminder>
keep
[Subagent Context] meta
keep\x00`;
    const once = stripInjectedBlocks(input);
    const twice = stripInjectedBlocks(once);
    expect(twice).toBe(once);
  });

  it("on already-clean text, strip(x) === x", () => {
    const clean = "no markers here, just prose with `code` and *emphasis*";
    expect(stripInjectedBlocks(clean)).toBe(clean);
  });
});

describe("sanitize — strict mode (classification)", () => {
  it("collapses runs of whitespace and trims, after stripping", () => {
    const input = "  before \t\n <openviking-context>x</openviking-context>\n\n after  ";
    expect(sanitize(input)).toBe("before after");
  });

  it("returns empty string for marker-only input", () => {
    expect(sanitize("<openviking-context>only</openviking-context>")).toBe("");
  });
});

describe("pollution test", () => {
  it("the <openviking-context> block injected in turn N is fully removed before storing turn N+1", () => {
    // Simulate: plugin injected a recall block at the top of turn N's user
    // message. Turn N+1's user message ends up containing it verbatim
    // because the host's chat history concatenates prior content. The
    // sanitiser must strip every byte of the injected block so the
    // recall content never lands in OpenViking memory.
    const recallBlock = "<openviking-context>\n- [memory 80%] recalled item\n</openviking-context>";
    const userN = `${recallBlock}\n\nWhat did we decide about the auth migration?`;
    const userNPlus1 =
      `Previously you said: <copilot-context>cp recall</copilot-context>\n` +
      `${userN}\n` +
      `<system-reminder>internal</system-reminder>\n` +
      `Follow-up question.`;

    const stripped = stripInjectedBlocks(userNPlus1);

    expect(stripped).not.toContain("openviking-context");
    expect(stripped).not.toContain("copilot-context");
    expect(stripped).not.toContain("system-reminder");
    expect(stripped).not.toContain("recalled item");
    expect(stripped).not.toContain("cp recall");
    // The user's actual content survives.
    expect(stripped).toContain("auth migration");
    expect(stripped).toContain("Follow-up question.");
  });
});

describe("INJECTED_BLOCK_PATTERNS catalogue", () => {
  it("is exported as a non-empty readonly array of RegExps", () => {
    expect(Array.isArray(INJECTED_BLOCK_PATTERNS)).toBe(true);
    expect(INJECTED_BLOCK_PATTERNS.length).toBeGreaterThan(0);
    for (const p of INJECTED_BLOCK_PATTERNS) {
      expect(p).toBeInstanceOf(RegExp);
    }
  });
});
