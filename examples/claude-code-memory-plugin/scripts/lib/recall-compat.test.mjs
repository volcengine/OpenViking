import assert from "node:assert/strict";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import { buildServerAssembledBlock, fetchAssembledContext, unknownBodyFields } from "../shared/recall-core.mjs";

const EXTRA_FORBIDDEN = (...fields) => ({
  ok: false,
  status: 400,
  error: {
    code: "INVALID_ARGUMENT",
    message: "Invalid request parameters",
    details: {
      validation_errors: fields.map((field) => ({
        loc: ["body", field],
        message: "Extra inputs are not permitted",
        type: "extra_forbidden",
      })),
    },
  },
});

test("unknownBodyFields reads the fields a validation error names", () => {
  const res = EXTRA_FORBIDDEN("mode", "purpose", "max_tokens");
  assert.deepEqual(unknownBodyFields(res), ["mode", "purpose", "max_tokens"]);
});

test("unknownBodyFields ignores non-extra errors and never strips query", () => {
  const res = {
    ok: false,
    status: 400,
    error: {
      details: {
        validation_errors: [
          { loc: ["body", "query"], message: "Extra inputs are not permitted", type: "extra_forbidden" },
          { loc: ["body", "score_threshold"], message: "Input should be a number", type: "float_parsing" },
        ],
      },
    },
  };
  assert.deepEqual(unknownBodyFields(res), []);
});

test("fetchAssembledContext strips rejected fields and retries instead of going legacy", async () => {
  const legacyCachePath = join(mkdtempSync(join(tmpdir(), "ov-compat-")), "context-face.json");
  const bodies = [];
  const fetchJSON = async (path, init) => {
    const body = JSON.parse(init.body);
    bodies.push(body);
    const extras = ["mode", "purpose", "dedup_turns"].filter((field) => field in body);
    if (extras.length) return EXTRA_FORBIDDEN(...extras);
    return {
      ok: true,
      status: 200,
      result: {
        rendered: "remembered",
        entries: [{ uri: "viking://user/alice/memories/note.md", category: "memory", text: "remembered" }],
        stats: { used_tokens: 5 },
      },
    };
  };

  const events = [];
  const out = await fetchAssembledContext(
    fetchJSON,
    {},
    "what did we decide?",
    { sessionId: "s1", legacyCachePath, log: (stage, data) => events.push([stage, data]) },
  );

  assert.equal(out.rendered, "remembered");
  assert.equal(bodies.length, 2, "one strip pass, then success");
  assert.equal(bodies[1].mode, undefined);
  assert.equal(bodies[1].purpose, undefined);
  assert.equal(bodies[1].query, "what did we decide?");
  const stripEvents = events.filter(([stage]) => stage === "recall_context_face_fields_stripped");
  assert.equal(stripEvents.length, 1);
  assert.deepEqual(stripEvents[0][1].stripped.sort(), ["dedup_turns", "mode", "purpose"]);

  // A second call must not have been poisoned by a legacy marker.
  const again = await fetchAssembledContext(fetchJSON, {}, "again", { legacyCachePath });
  assert.ok(again, "the context face stays supported after a successful strip-retry");
});

test("fetchAssembledContext still goes legacy when the error names nothing strippable", async () => {
  const legacyCachePath = join(mkdtempSync(join(tmpdir(), "ov-compat-")), "context-face.json");
  const fetchJSON = async () => ({
    ok: false,
    status: 400,
    error: { message: "unexpected mode value" },
  });
  const events = [];
  const out = await fetchAssembledContext(
    fetchJSON,
    {},
    "query",
    { legacyCachePath, log: (stage) => events.push(stage) },
  );
  assert.equal(out, null);
  assert.ok(events.includes("recall_context_face_unsupported"));
});

test("fetchAssembledContext adapts the older memories shape into entries", async () => {
  const legacyCachePath = join(mkdtempSync(join(tmpdir(), "ov-compat-")), "context-face.json");
  const fetchJSON = async () => ({
    ok: true,
    status: 200,
    result: {
      memories: [
        { uri: "viking://user/alice/memories/skills/Build.md", context_type: "memory",
          abstract: "Skill: Build", score: 0.44 },
      ],
    },
  });
  const out = await fetchAssembledContext(fetchJSON, {}, "build", { legacyCachePath });
  assert.equal(out.entries.length, 1);
  assert.equal(out.entries[0].category, "memory");
  assert.equal(out.entries[0].text, "Skill: Build");
});

test("buildServerAssembledBlock renders old-shape memories into an injection block", async () => {
  const legacyCachePath = join(mkdtempSync(join(tmpdir(), "ov-compat-")), "context-face.json");
  const fetchJSON = async (path) => {
    if (path === "/api/v1/search/search") {
      return {
        ok: true,
        status: 200,
        result: {
          memories: [
            { uri: "viking://user/alice/memories/skills/Build.md", context_type: "memory",
              abstract: "Skill: Build — prefer make targets over raw scripts", score: 0.52 },
          ],
        },
      };
    }
    return { ok: false, status: 404 };
  };
  const block = await buildServerAssembledBlock(fetchJSON, {}, "how do we build?", { legacyCachePath });
  assert.ok(block.includes("<openviking-context>"));
  assert.ok(block.includes("viking://user/alice/memories/skills/Build.md"));
  assert.ok(block.includes("prefer make targets"));
});
