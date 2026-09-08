import assert from "node:assert/strict";
import test from "node:test";

import {
  TEXT_PART_MAX_BYTES,
  TEXT_TOTAL_MAX_BYTES,
  capTextPartBytes,
  capTextParts,
} from "./text-part-cap.mjs";

const MARKER_RE = /\n\.\.\. \[truncated, \d+ more chars\]$/;

// A lone surrogate does not survive a UTF-8 round-trip (it becomes U+FFFD),
// so round-trip equality proves the string is well-formed -- the same
// property the server's utf-8 encode requires.
function isUtf8RoundTrippable(s) {
  return Buffer.from(s, "utf8").toString("utf8") === s;
}

test("in-bounds well-formed text passes through unchanged", () => {
  assert.equal(capTextPartBytes("hello world"), "hello world");
  const exact = "a".repeat(TEXT_PART_MAX_BYTES);
  assert.equal(capTextPartBytes(exact), exact);
});

test("in-bounds text containing a lone surrogate is made well-formed", () => {
  const dirty = "abc\ud83d def";
  const cleaned = capTextPartBytes(dirty);
  assert.notEqual(cleaned, dirty);
  assert.ok(isUtf8RoundTrippable(cleaned));
  assert.match(cleaned, /^abc.* def$/);
});

test("oversized ascii is capped under the limit with a marker", () => {
  const capped = capTextPartBytes("x".repeat(254000));
  assert.ok(Buffer.byteLength(capped, "utf8") <= TEXT_PART_MAX_BYTES);
  assert.match(capped, MARKER_RE);
});

test("oversized multi-byte text is capped under the limit and well-formed", () => {
  const capped = capTextPartBytes("日本語テスト".repeat(20000));
  assert.ok(Buffer.byteLength(capped, "utf8") <= TEXT_PART_MAX_BYTES);
  assert.match(capped, MARKER_RE);
  assert.ok(isUtf8RoundTrippable(capped));
});

test("truncation never leaves an unpaired surrogate at any pair alignment", () => {
  // Sweep 0-3 three-byte chars before an emoji run so the slice boundary
  // lands on every possible surrogate-pair alignment.
  for (let k = 0; k <= 3; k++) {
    const input = "見".repeat(k) + "\u{1F600}".repeat(20000);
    const capped = capTextPartBytes(input);
    assert.ok(
      isUtf8RoundTrippable(capped),
      `k=${k}: capped output contains an unpaired surrogate`,
    );
    assert.ok(Buffer.byteLength(capped, "utf8") <= TEXT_PART_MAX_BYTES, `k=${k}: over cap`);
  }
});

test("tiny maxBytes terminates and degrades to marker-only output", () => {
  const capped = capTextPartBytes("x".repeat(100000), 10);
  assert.ok(Buffer.byteLength(capped, "utf8") < 100);
  assert.match(capped, MARKER_RE);
});

test("capTextParts enforces the per-message total budget across parts", () => {
  const parts = [];
  for (let i = 0; i < 6; i++) parts.push({ type: "text", text: "y".repeat(20000) });
  parts.push({ type: "tool", tool_name: "Read", tool_input: { big: "z".repeat(30000) } });

  const capped = capTextParts(parts);
  assert.equal(capped.length, parts.length);
  assert.strictEqual(capped[6], parts[6], "non-text parts pass through untouched");

  let totalTextBytes = 0;
  for (const p of capped) {
    if (p.type === "text") totalTextBytes += Buffer.byteLength(p.text, "utf8");
  }
  // Total stays within the budget plus small marker-only tails for parts
  // capped after the budget ran out.
  assert.ok(
    totalTextBytes <= TEXT_TOTAL_MAX_BYTES + parts.length * 64,
    `total text bytes ${totalTextBytes} exceeds budget`,
  );
});

test("capTextParts leaves in-bounds messages untouched", () => {
  const parts = [
    { type: "text", text: "short note" },
    { type: "tool", tool_name: "Bash", tool_output: "ok" },
  ];
  const capped = capTextParts(parts);
  assert.strictEqual(capped[0], parts[0]);
  assert.strictEqual(capped[1], parts[1]);
});
