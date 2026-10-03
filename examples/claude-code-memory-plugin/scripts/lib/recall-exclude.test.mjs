import assert from "node:assert/strict";
import test from "node:test";

// The auto-recall entry now passes cfg.recallExcludeUris into the recall
// options (mirroring the DSH entry's wiring for #5312). These tests pin the
// option-to-request plumbing the entries depend on (#5402).
import { buildContextSearchBody } from "../shared/recall-core.mjs";

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

test("non-array excludeUris is ignored rather than throwing", () => {
  const body = buildContextSearchBody({}, { excludeUris: "viking://user/default/skills" });
  assert.equal("exclude_uris" in body, false);
});

test("exclude_uris is capped at 200 entries", () => {
  const many = Array.from({ length: 250 }, (_, i) => `viking://user/u${i}/skills`);
  const body = buildContextSearchBody({}, { excludeUris: many });
  assert.equal(body.exclude_uris.length, 200);
});
