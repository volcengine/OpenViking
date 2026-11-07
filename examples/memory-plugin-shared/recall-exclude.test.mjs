import assert from "node:assert/strict";
import test from "node:test";

// The recall pipeline carries cfg.recallExcludeUris in two layers: the
// context-search request body (server-side exclusion, #5402) and the raw
// fallback path (searchAllSources), which never sends exclude_uris and can
// only honor the list client-side. These tests pin both layers.
import { buildContextSearchBody, buildRecallBlock } from "./lib/recall-core.mjs";

async function tempPath(name) {
  const { mkdtemp } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const { join } = await import("node:path");
  return join(await mkdtemp(join(tmpdir(), "ov-recall-")), name);
}

function fallbackFetch(memories) {
  return async (path) => {
    if (path === "/api/v1/search/search") return { ok: false, status: 503 };
    if (path === "/api/v1/search/recall") return { ok: false, status: 404 };
    if (path === "/api/v1/search/find") return { ok: true, result: { memories, skills: [] } };
    return { ok: false, status: 404 };
  };
}

test("excludeUris option lands in the context search body as exclude_uris", () => {
  const body = buildContextSearchBody(
    {},
    { excludeUris: ["viking://user/default/skills", "viking://agent/skills"] },
  );
  assert.deepEqual(body.exclude_uris, ["viking://user/default/skills", "viking://agent/skills"]);
});

test("no excludeUris option leaves exclude_uris unset", () => {
  const body = buildContextSearchBody({}, {});
  assert.equal("exclude_uris" in body, false);
});

test("the raw fallback drops an excluded subtree root and its children", async () => {
  const block = await buildRecallBlock(
    fallbackFetch([
      { uri: "viking://user/default/memories/keep.md", score: 0.9, abstract: "keep me", level: 1 },
      {
        uri: "viking://user/default/memories/secret/x.md",
        score: 0.95,
        abstract: "secret child",
        level: 1,
      },
    ]),
    {},
    "hello",
    {
      excludeUris: ["viking://user/default/memories/secret"],
      legacyCachePath: await tempPath("context-face.json"),
    },
  );

  assert.ok(block, "the non-excluded item must still be recalled");
  assert.match(block, /keep\.md/);
  assert.doesNotMatch(block, /secret/, "the excluded subtree must not reach the prompt");
});

test("the raw fallback keeps everything when no excludeUris are configured", async () => {
  const block = await buildRecallBlock(
    fallbackFetch([
      {
        uri: "viking://user/default/memories/secret/x.md",
        score: 0.95,
        abstract: "secret child",
        level: 1,
      },
    ]),
    {},
    "hello",
    { legacyCachePath: await tempPath("context-face.json") },
  );

  assert.ok(block);
  assert.match(block, /secret/);
});

test("an exact excluded URI is dropped by the raw fallback too", async () => {
  const block = await buildRecallBlock(
    fallbackFetch([
      {
        uri: "viking://user/default/memories/secret.md",
        score: 0.95,
        abstract: "exact match",
        level: 1,
      },
    ]),
    {},
    "hello",
    {
      excludeUris: ["viking://user/default/memories/secret.md"],
      legacyCachePath: await tempPath("context-face.json"),
    },
  );

  assert.equal(block, null, "the exact excluded document must not be recalled");
});
