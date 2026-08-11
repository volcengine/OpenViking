import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  compressRecallContext,
  normalizeCompressedContext,
  repairDigestUris,
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
  await compressRecallContext({
    query: "different", rendered, entries, runCompressor, cachePath,
  });

  assert.equal(calls, 2);
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

test("NO_RELEVANT_MEMORY is a successful empty compression result", async () => {
  const result = await compressRecallContext({
    query: "q",
    rendered: `<memory uri="viking://a">${"x".repeat(2000)}</memory>`,
    entries: [{ uri: "viking://a" }],
    runCompressor: async () => "NO_RELEVANT_MEMORY",
  });

  assert.deepEqual(result, { status: "empty", context: "" });
});
