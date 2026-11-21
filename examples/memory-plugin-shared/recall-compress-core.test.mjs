import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  compressRecallContext,
  normalizeCompressedContext,
  repairDigestUris,
  skipsCompressionForShortPrompt,
  userTypedPromptLength,
} from "./lib/recall-compress-core.mjs";

async function tempPath(name) {
  const dir = await mkdtemp(join(tmpdir(), "ov-compress-"));
  return join(dir, name);
}

test("compressed context keeps only citations to served URIs", () => {
  const served = "viking://user/u/memories/events/a.md";
  const normalized = normalizeCompressedContext([
    `- good 来源：${served}`,
    "- uncited fact",
    "- invented 来源：viking://unrelated/fake.md",
  ].join("\n"));

  assert.equal(
    repairDigestUris(normalized, [served]),
    `OpenViking memory digest:\n- good 来源：${served}`,
  );
});

test("URI repair validates every citation while preserving served paths with spaces", () => {
  const plain = "viking://user/u/a.md";
  const served = "viking://user/u/memories/preferences/cross runtime.md";
  assert.equal(repairDigestUris(`- raw source: ${served}`, [served]), `- raw source: ${served}`);
  assert.equal(
    repairDigestUris("- escaped source: viking://user/u/memories/preferences/cross%20runtime.md", [served]),
    `- escaped source: ${served}`,
  );
  assert.equal(repairDigestUris(`- near miss source: ${plain}.evil`, [plain]), `- near miss source: ${plain}`);
  assert.equal(repairDigestUris(`- mixed source: ${plain} and viking://evil/x.md`, [plain]), "");
  assert.equal(repairDigestUris(`- mixed source: ${served} and viking://evil/x.md`, [served]), "");
});

test("short compression input returns the caller's bounded context", async () => {
  let called = false;
  const result = await compressRecallContext({
    query: "q",
    rendered: "full compressor input",
    shortContext: "bounded display context",
    cfg: { recallCompressMinInputChars: 100 },
    runCompressor: async () => {
      called = true;
      return "NO_RELEVANT_MEMORY";
    },
  });

  assert.deepEqual(result, { status: "ok", context: "bounded display context" });
  assert.equal(called, false);
});

test("the prompt threshold measures only what the user typed", () => {
  const reminder = `<system-reminder>${"context ".repeat(200)}</system-reminder>`;
  assert.equal(userTypedPromptLength(`${reminder}\n\nyes`), 3);
  assert.equal(userTypedPromptLength(`${reminder}\n${reminder}\nproceed`), 7);
  assert.equal(userTypedPromptLength("yes"), 3);
  // Below the threshold, at it, and above it, with the injected blocks present.
  assert.equal(skipsCompressionForShortPrompt({ recallCompressMinPromptChars: 120 }, `${reminder}\nyes`), true);
  assert.equal(
    skipsCompressionForShortPrompt({ recallCompressMinPromptChars: 3 }, `${reminder}\nyes`),
    false,
  );
  assert.equal(
    skipsCompressionForShortPrompt({ recallCompressMinPromptChars: 120 }, `${reminder}\n${"x".repeat(120)}`),
    false,
  );
  // 0 is the default and keeps compression unconditional, as before.
  assert.equal(skipsCompressionForShortPrompt({}, "yes"), false);
  assert.equal(skipsCompressionForShortPrompt({ recallCompressMinPromptChars: 0 }, "yes"), false);
});

test("a short prompt skips the compressor and keeps the raw context", async () => {
  let called = false;
  const result = await compressRecallContext({
    query: "yes",
    rendered: "full compressor input that is longer than the input threshold",
    shortContext: "raw recall block",
    cfg: { recallCompressMinInputChars: 0, recallCompressMinPromptChars: 120 },
    runCompressor: async () => {
      called = true;
      return "- compressed fact 来源：viking://user/u/a.md";
    },
  });

  assert.deepEqual(result, { status: "ok", context: "raw recall block" });
  assert.equal(called, false);
});

test("a long prompt still compresses when a prompt threshold is set", async () => {
  let called = 0;
  const result = await compressRecallContext({
    query: "explain how the retrieval pipeline ranks skills against memories",
    rendered: "full compressor input",
    shortContext: "raw recall block",
    cfg: { recallCompressMinInputChars: 0, recallCompressMinPromptChars: 20 },
    runCompressor: async () => {
      called += 1;
      return "- compressed fact 来源：viking://user/u/a.md";
    },
  });

  assert.equal(called, 1);
  assert.match(result.context, /compressed fact/);
});

test("compression cache is reused only for the same request", async () => {
  const cachePath = await tempPath("digest.json");
  const rendered = `<memory uri="viking://a">${"x".repeat(2000)}</memory>`;
  const entries = [{ uri: "viking://a" }];
  let calls = 0;
  const runCompressor = async () => {
    calls += 1;
    return "- fact 来源：viking://a";
  };

  await compressRecallContext({
    query: "first", rendered, entries, runCompressor, cachePath,
  });
  await compressRecallContext({
    query: "first", rendered, entries, runCompressor, cachePath,
  });
  const cached = JSON.parse(await readFile(cachePath, "utf8"));
  await writeFile(cachePath, JSON.stringify({ ...cached, digest: "OpenViking memory digest:" }));
  await compressRecallContext({
    query: "first", rendered, entries, runCompressor, cachePath,
  });
  await compressRecallContext({
    query: "different", rendered, entries, runCompressor, cachePath,
  });

  assert.equal(calls, 3);
});

test("unusable compressor output triggers the caller fallback", async () => {
  const result = await compressRecallContext({
    query: "q",
    rendered: `<memory uri="viking://a">${"x".repeat(2000)}</memory>`,
    entries: [{ uri: "viking://a" }],
    runCompressor: async () => "I could not find anything relevant.",
  });

  assert.deepEqual(result, { status: "failed", context: "" });
});

test("compression fails when URI repair rejects every bullet", async () => {
  const result = await compressRecallContext({
    query: "q",
    rendered: `<memory uri="viking://served/a.md">${"x".repeat(2000)}</memory>`,
    entries: [{ uri: "viking://served/a.md" }],
    runCompressor: async () => "- invented source: viking://unrelated/fake.md",
  });

  assert.deepEqual(result, { status: "failed", context: "" });
});

test("NO_RELEVANT_MEMORY is a successful empty compression result", async () => {
  const result = await compressRecallContext({
    query: "q",
    rendered: `<memory uri="viking://a">${"x".repeat(2000)}</memory>`,
    entries: [{ uri: "viking://a" }],
    runCompressor: async () => "NO_RELEVANT_MEMORY",
  });

  assert.deepEqual(result, { status: "empty", context: "" });
});
