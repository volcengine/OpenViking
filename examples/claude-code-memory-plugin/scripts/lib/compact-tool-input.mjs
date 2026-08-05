/**
 * Tool input compaction — shared between auto-capture.mjs and subagent-stop.mjs.
 *
 * Write/Edit tool inputs carry full file contents (often 7KB+ of source code)
 * that inflate session storage, dilute vector embeddings, and waste VLM tokens
 * during memory extraction.  The memory extractor only needs to know *what* was
 * changed and *where*, not the full content.
 *
 * Two export paths:
 *   compactToolInputForProse() → string  (for inline text: `[tool: X]\n...`)
 *   compactToolInputForPart()  → object  (for structured parts: server expects Dict)
 */

const PREVIEW_CHARS = 200;
const DIFF_PREVIEW_CHARS = 150;

export const TOOL_INPUT_POLICIES = {
  // Full preservation — short inputs with high signal (paths, queries, search params)
  full: new Set(["Read", "Glob", "Grep", "LSP", "WebFetch", "WebSearch", "Skill"]),
  // Summary only — inputs that carry full file contents or verbose metadata
  summary: new Set(["Write", "Edit", "Bash", "TaskCreate", "TaskUpdate"]),
};

function formatString(value) {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/**
 * Build a compacted *object* from a raw tool input.  Callers use this as the
 * shared compaction kernel; compactToolInputForProse JSON.stringify()s the
 * result, compactToolInputForPart returns it directly.
 */
function buildCompactObject(toolName, obj) {
  // Normalize: coerce JSON strings to objects so obj.content / obj.command work.
  if (typeof obj === "string") {
    try { obj = JSON.parse(obj); } catch { return obj; }
  }
  if (!obj || typeof obj !== "object") return obj;

  if (toolName === "Write") {
    const content = String(obj.content ?? "");
    const lines = content.split("\n").length;
    return {
      file_path: obj.file_path,
      content_summary: `${lines} lines, ${content.length} chars`,
      content_preview: content.slice(0, PREVIEW_CHARS),
    };
  }

  if (toolName === "Edit") {
    const oldStr = String(obj.old_string ?? "");
    const newStr = String(obj.new_string ?? "");
    return {
      file_path: obj.file_path,
      replace_all: obj.replace_all || false,
      old_summary: `${oldStr.length} chars`,
      old_preview: oldStr.slice(0, DIFF_PREVIEW_CHARS),
      new_summary: `${newStr.length} chars`,
      new_preview: newStr.slice(0, DIFF_PREVIEW_CHARS),
    };
  }

  if (toolName === "Bash") {
    return { command: obj.command };
  }

  if (toolName === "TaskCreate" || toolName === "TaskUpdate") {
    const summary = {};
    if (obj.subject) summary.subject = obj.subject;
    if (obj.status) summary.status = obj.status;
    if (obj.taskId) summary.taskId = obj.taskId;
    return summary;
  }

  // Unknown tool — pass through unchanged.
  return obj;
}

/**
 * Compact and return a **string** — for prose / inline text paths.
 *
 * Non-summary tools: JSON.stringify(value, null, 2), truncated to maxChars.
 * Summary tools:     compact object → JSON.stringify → truncated.
 */
export function compactToolInputForProse(toolName, value, maxChars = 0) {
  if (typeof value === "string") return value;

  if (!TOOL_INPUT_POLICIES.summary.has(toolName)) {
    const raw = formatString(value);
    if (maxChars > 0 && raw.length > maxChars) {
      return raw.slice(0, maxChars) + `\n... [truncated, ${raw.length - maxChars} more chars]`;
    }
    return raw;
  }

  try {
    const compact = buildCompactObject(toolName, value);
    let result = JSON.stringify(compact);
    if (maxChars > 0 && result.length > maxChars) {
      result = result.slice(0, maxChars) + "\n... [truncated]";
    }
    return result;
  } catch {
    return formatString(value);
  }
}

/**
 * Compact and return an **object** — for structured parts (server expects
 * `tool_input: Optional[Dict[str, Any]]`).  Must NOT return a JSON string.
 *
 * Non-summary tools: pass through raw value unchanged.
 * Summary tools:     compact object (no JSON.stringify).
 */
export function compactToolInputForPart(toolName, value) {
  if (!value || typeof value !== "object") return value;

  if (!TOOL_INPUT_POLICIES.summary.has(toolName)) {
    return value; // full preservation
  }

  try {
    return buildCompactObject(toolName, value);
  } catch {
    return value;
  }
}

// ---------------------------------------------------------------------------
// Inline tests — node --test scripts/lib/compact-tool-input.mjs
// ---------------------------------------------------------------------------
import test from "node:test";
import assert from "node:assert/strict";

test("compactToolInputForPart returns object for Write", () => {
  const input = { file_path: "/tmp/x.txt", content: "hello\nworld" };
  const result = compactToolInputForPart("Write", input);
  assert.equal(typeof result, "object");
  assert.ok(!Array.isArray(result));
  assert.equal(result.file_path, "/tmp/x.txt");
  assert.match(result.content_summary, /2 lines/);
  assert.equal(typeof result.content_preview, "string");
});

test("compactToolInputForPart returns object for Edit", () => {
  const input = { file_path: "/tmp/y.txt", old_string: "aaa", new_string: "bbb" };
  const result = compactToolInputForPart("Edit", input);
  assert.equal(typeof result, "object");
  assert.equal(result.file_path, "/tmp/y.txt");
  assert.equal(result.replace_all, false);
  assert.equal(result.old_summary, "3 chars");
  assert.equal(result.new_summary, "3 chars");
});

test("compactToolInputForPart returns object for Bash", () => {
  const result = compactToolInputForPart("Bash", { command: "git status" });
  assert.equal(typeof result, "object");
  assert.equal(result.command, "git status");
});

test("compactToolInputForPart returns object for TaskCreate", () => {
  const result = compactToolInputForPart("TaskCreate", { subject: "Fix bug", status: "pending" });
  assert.equal(typeof result, "object");
  assert.equal(result.subject, "Fix bug");
  assert.equal(result.status, "pending");
  assert.equal(result.taskId, undefined);
});

test("compactToolInputForPart passes through Read (full preservation)", () => {
  const input = { file_path: "/tmp/z.txt", content: "data" };
  const result = compactToolInputForPart("Read", input);
  assert.deepEqual(result, input); // unchanged
});

test("compactToolInputForPart passes through string", () => {
  assert.equal(compactToolInputForPart("Write", "just a string"), "just a string");
});

test("compactToolInputForPart passes through null/undefined", () => {
  assert.equal(compactToolInputForPart("Write", null), null);
  assert.equal(compactToolInputForPart("Write", undefined), undefined);
});

test("compactToolInputForProse returns string for Write", () => {
  const input = { file_path: "/tmp/w.txt", content: "one line" };
  const result = compactToolInputForProse("Write", input);
  assert.equal(typeof result, "string");
  // Contains the key fields
  assert.match(result, /w\.txt/);
  assert.match(result, /content_summary/);
});

test("compactToolInputForProse returns string for Read", () => {
  const input = { file_path: "/tmp/r.txt" };
  const result = compactToolInputForProse("Read", input);
  assert.equal(typeof result, "string");
});

test("compactToolInputForProse truncates to maxChars", () => {
  const input = { file_path: "/tmp/long.txt", content: "x".repeat(500) };
  const result = compactToolInputForProse("Write", input, 150);
  assert.equal(typeof result, "string");
  assert.ok(result.length <= 180); // 100 + truncation suffix
  assert.match(result, /truncated/);
});

test("compactToolInputForProse passes string through unchanged", () => {
  assert.equal(compactToolInputForProse("Write", "raw string", 100), "raw string");
});

test("structured parts: all summary tools return object from compactToolInputForPart", () => {
  for (const tool of TOOL_INPUT_POLICIES.summary) {
    const input = { file_path: "/p", content: "x", command: "x", subject: "x", old_string: "x", new_string: "x" };
    const result = compactToolInputForPart(tool, input);
    assert.equal(typeof result, "object", `${tool}: expected object, got ${typeof result}`);
    assert.ok(!Array.isArray(result), `${tool}: expected plain object, got array`);
  }
});

test("structured parts: all full tools pass through unchanged", () => {
  for (const tool of TOOL_INPUT_POLICIES.full) {
    const input = { file_path: "/f", content: "data" };
    const result = compactToolInputForPart(tool, input);
    assert.deepEqual(result, input, `${tool}: expected pass-through`);
  }
});
