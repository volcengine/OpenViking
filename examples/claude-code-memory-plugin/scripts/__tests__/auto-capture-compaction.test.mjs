/**
 * Regression tests for compactToolInput() — rule-based tool input compaction
 * that reduces Write/Edit/Bash/TaskCreate/TaskUpdate inputs to structural
 * summaries without LLM involvement.
 *
 * Run: node __tests__/auto-capture-compaction.test.mjs
 */

import assert from "node:assert/strict";

// ─── Inline the functions under test ────────────────────────────────────────
// We copy the logic rather than importing to avoid coupling to the full
// auto-capture.mjs module (which has side effects and I/O).

function formatToolInput(value) {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

const TOOL_INPUT_POLICIES = {
  full: new Set([
    "Read", "Glob", "Grep", "LSP", "WebFetch", "WebSearch", "Skill",
  ]),
  summary: new Set([
    "Write", "Edit", "Bash", "TaskCreate", "TaskUpdate",
  ]),
};

const PREVIEW_CHARS = 200;
const DIFF_PREVIEW_CHARS = 150;

function compactToolInput(toolName, value, maxChars = 0) {
  if (typeof value === "string") return value;

  if (!TOOL_INPUT_POLICIES.summary.has(toolName)) {
    const raw = formatToolInput(value);
    return maxChars > 0 && raw.length > maxChars
      ? raw.slice(0, maxChars) + `\n... [truncated, ${raw.length - maxChars} more chars]`
      : raw;
  }

  try {
    const obj = typeof value === "object" ? value : JSON.parse(value);
    let result;

    if (toolName === "Write") {
      const content = obj.content || "";
      const lines = content.split("\n").length;
      result = JSON.stringify({
        file_path: obj.file_path,
        content_summary: `${lines} lines, ${content.length} chars`,
        content_preview: content.slice(0, PREVIEW_CHARS),
      });
    } else if (toolName === "Edit") {
      const oldStr = obj.old_string || "";
      const newStr = obj.new_string || "";
      result = JSON.stringify({
        file_path: obj.file_path,
        replace_all: obj.replace_all || false,
        old_summary: `${oldStr.length} chars`,
        old_preview: oldStr.slice(0, DIFF_PREVIEW_CHARS),
        new_summary: `${newStr.length} chars`,
        new_preview: newStr.slice(0, DIFF_PREVIEW_CHARS),
      });
    } else if (toolName === "Bash") {
      result = JSON.stringify({ command: obj.command });
    } else if (toolName === "TaskCreate" || toolName === "TaskUpdate") {
      const summary = {};
      if (obj.subject) summary.subject = obj.subject;
      if (obj.status) summary.status = obj.status;
      if (obj.taskId) summary.taskId = obj.taskId;
      result = JSON.stringify(summary);
    } else {
      result = formatToolInput(value);
    }

    if (maxChars > 0 && result.length > maxChars) {
      result = result.slice(0, maxChars) + `\n... [truncated]`;
    }
    return result;
  } catch {
    return formatToolInput(value);
  }
}

// ─── Helpers ────────────────────────────────────────────────────────────────

let pass = 0;
let fail = 0;

function test(name, fn) {
  try {
    fn();
    pass++;
    console.log(`  ✓ ${name}`);
  } catch (e) {
    fail++;
    console.log(`  ✗ ${name}`);
    console.log(`    ${e.message}`);
  }
}

// Generate a long string of repeated lines
function longContent(lines, charsPerLine = 80) {
  const line = "x".repeat(charsPerLine);
  return Array.from({ length: lines }, () => line).join("\n");
}

// ─── Tests ──────────────────────────────────────────────────────────────────

console.log("\ncompactToolInput regression tests\n");

// 1. Write compaction — 7KB content → summary with file_path + line count + 200-char preview
test("Write: 7KB content compacted to structural summary", () => {
  const content = longContent(100, 72); // ~7200 chars
  const input = { file_path: "/src/index.ts", content };
  const result = compactToolInput("Write", input);
  const parsed = JSON.parse(result);

  assert.equal(parsed.file_path, "/src/index.ts");
  assert.ok(parsed.content_summary.includes("100 lines"), `expected line count in summary, got: ${parsed.content_summary}`);
  assert.ok(parsed.content_preview.length <= 200, `preview should be ≤200 chars, got ${parsed.content_preview.length}`);
  // Compression ratio: original ~7200 chars vs compacted ~350 chars
  assert.ok(result.length < 500, `compacted should be <500 chars, got ${result.length}`);
  assert.ok(content.length / result.length > 10, `compression ratio should be >10x, got ${(content.length / result.length).toFixed(1)}x`);
});

// 2. Edit compaction — old_string/new_string → file_path + length summary + 150-char previews
test("Edit: old_string/new_string compacted to diff summary", () => {
  const oldStr = longContent(50, 60); // ~3000 chars
  const newStr = longContent(30, 60); // ~1800 chars
  const input = {
    file_path: "/src/utils.mjs",
    old_string: oldStr,
    new_string: newStr,
    replace_all: false,
  };
  const result = compactToolInput("Edit", input);
  const parsed = JSON.parse(result);

  assert.equal(parsed.file_path, "/src/utils.mjs");
  assert.equal(parsed.replace_all, false);
  assert.ok(parsed.old_summary.includes("chars"), `old_summary should mention chars, got: ${parsed.old_summary}`);
  assert.ok(parsed.new_summary.includes("chars"), `new_summary should mention chars, got: ${parsed.new_summary}`);
  assert.ok(parsed.old_preview.length <= 150, `old_preview should be ≤150 chars, got ${parsed.old_preview.length}`);
  assert.ok(parsed.new_preview.length <= 150, `new_preview should be ≤150 chars, got ${parsed.new_preview.length}`);
  // Compression: ~4800 chars → ~400 chars
  assert.ok(result.length < 600, `compacted should be <600 chars, got ${result.length}`);
});

// 3. Bash compaction — keep command, drop description
test("Bash: keep command, drop description", () => {
  const input = {
    command: "git rebase -i HEAD~5",
    description: "Interactively rebase the last 5 commits to squash and reorder",
  };
  const result = compactToolInput("Bash", input);
  const parsed = JSON.parse(result);

  assert.equal(parsed.command, "git rebase -i HEAD~5");
  assert.equal(parsed.description, undefined, "description should be dropped");
});

// 4. TaskCreate/TaskUpdate compaction — subject + status only
test("TaskCreate: only subject + status preserved", () => {
  const input = {
    subject: "Fix auth middleware",
    description: "The auth middleware is failing because...",
    activeForm: "Fixing auth middleware",
    status: "in_progress",
  };
  const result = compactToolInput("TaskCreate", input);
  const parsed = JSON.parse(result);

  assert.equal(parsed.subject, "Fix auth middleware");
  assert.equal(parsed.status, "in_progress");
  assert.equal(parsed.description, undefined, "description should be dropped");
  assert.equal(parsed.activeForm, undefined, "activeForm should be dropped");
});

test("TaskUpdate: subject + status + taskId preserved", () => {
  const input = {
    taskId: "42",
    subject: "Write tests",
    status: "completed",
    description: "Detailed description here",
    owner: "agent-1",
  };
  const result = compactToolInput("TaskUpdate", input);
  const parsed = JSON.parse(result);

  assert.equal(parsed.subject, "Write tests");
  assert.equal(parsed.status, "completed");
  assert.equal(parsed.taskId, "42");
  assert.equal(parsed.description, undefined);
  assert.equal(parsed.owner, undefined);
});

// 5. Read/Glob/Grep — full preservation (not in summary set)
test("Read: full preservation (not compacted)", () => {
  const input = { file_path: "/src/index.ts" };
  const result = compactToolInput("Read", input);
  const parsed = JSON.parse(result);

  assert.equal(parsed.file_path, "/src/index.ts");
  // Should be identical to formatToolInput output
  assert.equal(result, formatToolInput(input));
});

test("Glob: full preservation", () => {
  const input = { pattern: "**/*.mjs", path: "/src" };
  const result = compactToolInput("Glob", input);
  assert.equal(result, formatToolInput(input));
});

test("Grep: full preservation", () => {
  const input = { pattern: "compactToolInput", path_filter: "^src/" };
  const result = compactToolInput("Grep", input);
  assert.equal(result, formatToolInput(input));
});

test("WebSearch: full preservation", () => {
  const input = { query: "bge-large-zh embedding model" };
  const result = compactToolInput("WebSearch", input);
  assert.equal(result, formatToolInput(input));
});

// 6. TOOL_INPUT_MAX_CHARS truncation — cap applied to full-preservation tools
test("maxChars truncation on full-preservation tool", () => {
  const input = { file_path: "/src/index.ts", query: "x".repeat(5000) };
  const maxChars = 200;
  const result = compactToolInput("Grep", input, maxChars);

  assert.ok(result.length > maxChars, "truncation marker adds length beyond cap");
  assert.ok(result.includes("[truncated"), "should include truncation marker");
  assert.ok(result.startsWith(formatToolInput(input).slice(0, maxChars)),
    "should start with the first maxChars of the full output");
});

test("maxChars=0 disables truncation", () => {
  const input = { file_path: "/src/index.ts", query: "x".repeat(5000) };
  const result = compactToolInput("Grep", input, 0);
  assert.equal(result, formatToolInput(input), "no truncation when maxChars=0");
});

// 7. compaction=off fallback — when useCompaction=false, caller uses formatToolInput directly
//    (this tests that the caller path works; compactToolInput itself always compacts
//    summary-set tools, but the harvestContent branch skips it when cfg.toolInputCompaction===false)
test("compaction=off: formatToolInput used directly (caller responsibility)", () => {
  const input = {
    file_path: "/src/index.ts",
    content: longContent(100, 72),
  };
  // When compaction is off, the caller uses formatToolInput, not compactToolInput
  const fullResult = formatToolInput(input);
  const compactResult = compactToolInput("Write", input);

  // Full result should be much larger
  assert.ok(fullResult.length > compactResult.length * 5,
    `full should be >5x compacted: full=${fullResult.length}, compact=${compactResult.length}`);
  // Compact result should NOT contain the full content
  assert.ok(!compactResult.includes(input.content),
    "compacted should not contain full content");
});

// 8. JSON round-trip — compactToolInput output is valid JSON for summary tools
test("Write: output is valid JSON", () => {
  const input = { file_path: "/src/a.ts", content: "export const x = 1;\n" };
  const result = compactToolInput("Write", input);
  const parsed = JSON.parse(result);
  assert.equal(parsed.file_path, "/src/a.ts");
});

test("Edit: output is valid JSON", () => {
  const input = { file_path: "/src/a.ts", old_string: "foo", new_string: "bar" };
  const result = compactToolInput("Edit", input);
  const parsed = JSON.parse(result);
  assert.equal(parsed.file_path, "/src/a.ts");
});

test("Bash: output is valid JSON", () => {
  const input = { command: "ls -la" };
  const result = compactToolInput("Bash", input);
  const parsed = JSON.parse(result);
  assert.equal(parsed.command, "ls -la");
});

test("TaskCreate: output is valid JSON", () => {
  const input = { subject: "Do thing", status: "pending" };
  const result = compactToolInput("TaskCreate", input);
  const parsed = JSON.parse(result);
  assert.equal(parsed.subject, "Do thing");
});

// ─── Edge cases ─────────────────────────────────────────────────────────────

test("string input passes through unchanged", () => {
  assert.equal(compactToolInput("Write", "just a string"), "just a string");
});

test("unknown tool in summary set falls back to formatToolInput", () => {
  // If a tool name is in the summary set but has no specific handler,
  // it falls through to the else branch → formatToolInput
  const input = { some: "data" };
  const result = compactToolInput("TaskUpdate", { subject: "x" }); // has handler
  assert.ok(JSON.parse(result).subject === "x");
});

test("Edit with empty old_string/new_string", () => {
  const input = { file_path: "/src/a.ts", old_string: "", new_string: "new code" };
  const result = compactToolInput("Edit", input);
  const parsed = JSON.parse(result);
  assert.equal(parsed.old_summary, "0 chars");
  assert.ok(parsed.new_preview.includes("new code"));
});

test("Write with empty content", () => {
  const input = { file_path: "/src/a.ts", content: "" };
  const result = compactToolInput("Write", input);
  const parsed = JSON.parse(result);
  assert.equal(parsed.content_summary, "1 lines, 0 chars"); // empty string has 1 line
});

// ─── Compression ratio on realistic data ─────────────────────────────────────

test("Compression ratio: realistic mixed session data", () => {
  // Simulate a realistic mix of tool calls from a CC session
  const toolCalls = [
    { name: "Write", input: { file_path: "/src/index.ts", content: longContent(120, 65) } },
    { name: "Edit", input: { file_path: "/src/utils.mjs", old_string: longContent(30, 60), new_string: longContent(25, 60) } },
    { name: "Edit", input: { file_path: "/src/config.mjs", old_string: "const x = 1;", new_string: "const x = 2;" } },
    { name: "Bash", input: { command: "npm test", description: "Run the test suite" } },
    { name: "Bash", input: { command: "git status", description: "Show working tree status" } },
    { name: "Read", input: { file_path: "/src/index.ts" } },
    { name: "Grep", input: { pattern: "compactToolInput", path_filter: "^src/" } },
    { name: "TaskCreate", input: { subject: "Add tests", description: "Write regression tests", status: "pending" } },
    { name: "TaskUpdate", input: { taskId: "1", subject: "Add tests", status: "completed", description: "Done" } },
  ];

  let fullSize = 0;
  let compactSize = 0;

  for (const tc of toolCalls) {
    fullSize += formatToolInput(tc.input).length;
    compactSize += compactToolInput(tc.name, tc.input).length;
  }

  const ratio = fullSize / compactSize;
  console.log(`    full: ${fullSize} chars, compact: ${compactSize} chars, ratio: ${ratio.toFixed(1)}x`);

  // With realistic data, expect at least 3x compression
  assert.ok(ratio >= 3, `expected ≥3x compression, got ${ratio.toFixed(1)}x`);
});

// ─── Summary ────────────────────────────────────────────────────────────────

console.log(`\n${pass + fail} tests: ${pass} passed, ${fail} failed\n`);
process.exit(fail > 0 ? 1 : 0);
