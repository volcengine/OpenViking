import test from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  compressRecallContext,
  DIGEST_HEADER,
  normalizeCompressedContext,
  recallDigestCacheKey,
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

const longServed = `viking://resources/${"a".repeat(136)}.md`;

async function compressOutput(raw, entries = [{ uri: longServed }], cfg = {}) {
  return compressRecallContext({
    query: "q",
    rendered: `<memory>${"input ".repeat(400)}</memory>`,
    entries,
    cfg,
    runCompressor: async () => raw,
  });
}

test("a long bullet keeps its full served citation within the 500-character body limit", async () => {
  const result = await compressOutput(`- ${"x".repeat(350)} source: ${longServed}`);
  assert.equal(result.status, "ok");
  assert.ok(result.context.endsWith(longServed));
  assert.equal(result.context.split("\n")[1].slice(2).length, 500);
});

test("repair runs before budgeting a long near-match citation", async () => {
  const near = longServed.replace(".md", ".m");
  const result = await compressOutput(`- ${"x".repeat(350)} source: ${near}`);
  assert.equal(result.status, "ok");
  assert.ok(result.context.endsWith(longServed));
  assert.equal(result.context.split("\n")[1].slice(2).length, 500);
});

test("star and whitespace bullet markers receive the same every-citation validation", async () => {
  const served = "viking://user/u/a.md";
  for (const marker of ["* ", "-\t", "*\t"]) {
    assert.deepEqual(
      await compressOutput(`${marker}mixed source: ${served} and viking://unrelated/forged.md`, [{ uri: served }]),
      { status: "failed", context: "" },
    );
    assert.deepEqual(
      await compressOutput(`${marker}fact source: ${served}.evil`, [{ uri: served }]),
      { status: "ok", context: `${DIGEST_HEADER}\n- fact source: ${served}` },
    );
  }
});

test("budgeting preserves served paths with spaces and multiple citations", async () => {
  const spaced = `viking://resources/${"a".repeat(80)} cross runtime.md`;
  const plain = "viking://user/u/a.md";
  const result = await compressOutput(
    `* ${"x".repeat(450)} source: ${spaced} and ${plain}`,
    [{ uri: spaced }, { uri: plain }],
  );
  assert.equal(result.status, "ok");
  assert.ok(result.context.endsWith(`${spaced} and ${plain}`));
  assert.equal(result.context.split("\n")[1].slice(2).length, 500);
  assert.deepEqual(
    await compressOutput(`* source: ${spaced} and viking://unrelated/forged.md`, [{ uri: spaced }]),
    { status: "failed", context: "" },
  );
});

test("a citation that cannot fit the bullet budget is dropped whole", async () => {
  const oversized = `viking://resources/${"a".repeat(500)}.md`;
  assert.deepEqual(
    await compressOutput(`- source: ${oversized}`, [{ uri: oversized }]),
    { status: "failed", context: "" },
  );
  const short = "viking://user/u/a.md";
  const result = await compressOutput(
    `- source: ${oversized}\n- normal source: ${short}`,
    [{ uri: oversized }, { uri: short }],
  );
  assert.deepEqual(result, { status: "ok", context: `${DIGEST_HEADER}\n- normal source: ${short}` });
});

test("the whole digest budget keeps complete citations when more bullets are configured", async () => {
  const entries = Array.from({ length: 9 }, (_, index) => ({ uri: `viking://resources/topic-${index}.md` }));
  const raw = entries.map(({ uri }) => `- ${"x".repeat(480)} source: ${uri}`).join("\n");
  const result = await compressOutput(raw, entries, { recallCompressMaxBullets: 9 });
  assert.equal(result.status, "ok");
  assert.equal(result.context.length, 4000);
  const bullets = result.context.split("\n").slice(1);
  assert.equal(bullets.length, 8);
  for (const [index, bullet] of bullets.entries()) {
    assert.ok(bullet.endsWith(entries[index].uri));
    assert.ok(bullet.slice(2).length <= 500);
  }
});

test("a small whole-context budget omits citations that cannot fit instead of slicing them", () => {
  const served = "viking://user/u/a.md";
  const first = `- ${"x".repeat(100)} source: ${served}`;
  const digest = normalizeCompressedContext(`${first}\n- source: ${longServed}`, 100, 2);
  assert.equal(digest.length, 100);
  assert.ok(digest.endsWith(served));
  assert.equal(digest.split("\n").length, 2);
  assert.equal(normalizeCompressedContext(`- source: ${longServed}`, 100), null);
  assert.equal(normalizeCompressedContext(`- source: ${served}`, 1), `${DIGEST_HEADER}\n- source: ${served}`);
});

async function cachedOutput(digest, runCompressor, entries = [{ uri: longServed }]) {
  const cachePath = await tempPath("digest.json");
  const request = { query: "cached", rendered: "input ".repeat(400).trim(), entries };
  await writeFile(cachePath, JSON.stringify({ key: recallDigestCacheKey(request), digest }));
  return compressRecallContext({ ...request, runCompressor, cachePath });
}

test("cached partial and overlong citations are repaired and budgeted before reuse", async () => {
  for (const digest of [
    `${DIGEST_HEADER}\n- ${"x".repeat(350)} source: ${longServed}`,
    `${DIGEST_HEADER}\n- ${("x".repeat(350) + " source: " + longServed).slice(0, 500)}`,
  ]) {
    let calls = 0;
    const result = await cachedOutput(digest, async () => { calls += 1; return "NO_RELEVANT_MEMORY"; });
    assert.equal(result.status, "ok");
    assert.ok(result.context.endsWith(longServed));
    assert.equal(result.context.split("\n")[1].slice(2).length, 500);
    assert.equal(calls, 0);
  }
});

test("invalid cached citations and cached sentinel trigger compression again", async () => {
  for (const digest of [
    "* invented source: viking://unrelated/forged.md",
    "NO_RELEVANT_MEMORY",
    `- source: viking://resources/${"b".repeat(500)}.md`,
  ]) {
    let calls = 0;
    const result = await cachedOutput(digest, async () => { calls += 1; return `- fresh source: ${longServed}`; });
    assert.deepEqual(result, { status: "ok", context: `${DIGEST_HEADER}\n- fresh source: ${longServed}` });
    assert.equal(calls, 1);
  }
});

test("URI validation still derives citations from full input when entries are absent", async () => {
  const result = await compressRecallContext({
    query: "q",
    rendered: `<memory uri="${longServed}">${"input ".repeat(400)}</memory>`,
    runCompressor: async () => `- ${"x".repeat(350)} source: ${longServed}`,
  });
  assert.equal(result.status, "ok");
  assert.ok(result.context.endsWith(longServed));
});

test("an empty served URI set retains the existing passthrough contract", async () => {
  assert.deepEqual(
    await compressOutput("- fact source: viking://unchecked/a.md", []),
    { status: "ok", context: `${DIGEST_HEADER}\n- fact source: viking://unchecked/a.md` },
  );
});

test("a bare citation scheme is rejected when served URIs are available", async () => {
  for (const raw of ["- fact source: viking://", "* fact source: viking:// and other text", '- fact source: viking://"']) {
    assert.deepEqual(await compressOutput(raw), { status: "failed", context: "" });
  }
});

test("cached bare citation schemes trigger compression again", async () => {
  for (const digest of ["- fact source: viking://", "- fact source: viking:// and other text", '- fact source: viking://"']) {
    let calls = 0;
    const result = await cachedOutput(digest, async () => { calls += 1; return `- fresh source: ${longServed}`; });
    assert.deepEqual(result, { status: "ok", context: `${DIGEST_HEADER}\n- fresh source: ${longServed}` });
    assert.equal(calls, 1);
  }
});

test("exact served URIs containing a scheme substring retain their original behavior", async () => {
  for (const served of ["viking://resources/a/viking://", "viking://resources/a/viking:// suffix.md", "viking://"]) {
    const expected = { status: "ok", context: `${DIGEST_HEADER}\n- fact source: ${served}` };
    assert.deepEqual(await compressOutput(`- fact source: ${served}`, [{ uri: served }]), expected);
    let calls = 0;
    assert.deepEqual(
      await cachedOutput(`- fact source: ${served}`, async () => { calls += 1; return "NO_RELEVANT_MEMORY"; }, [{ uri: served }]),
      expected,
    );
    assert.equal(calls, 0);
  }
});
