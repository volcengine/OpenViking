import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { KNOWN_PLUGIN_KEYS, unknownPluginKeys } from "./lib/doctor-core.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

/**
 * Both harness loaders read their knobs off one merged object — `cc` in Claude
 * Code, `cx` in Codex — so every `cc.<key>` / `cx.<key>` in those files is a
 * knob a user may legitimately set in ovcli.conf's `plugin` section.
 */
async function knobsReadBy(file, receiver) {
  const source = await readFile(join(ROOT, file), "utf-8");
  return new Set([...source.matchAll(new RegExp(`\\b${receiver}\\.([a-zA-Z_][a-zA-Z0-9_]*)`, "g"))].map((m) => m[1]));
}

test("the doctor's known-knob list matches what the loaders actually read", async () => {
  const read = new Set([
    ...(await knobsReadBy("examples/claude-code-memory-plugin/scripts/config.mjs", "cc")),
    ...(await knobsReadBy("examples/codex-memory-plugin/scripts/config.mjs", "cx")),
  ]);
  assert.ok(read.size > 20, `expected to find the knobs, got ${read.size}`);

  const missing = [...read].filter((key) => !KNOWN_PLUGIN_KEYS.has(key)).sort();
  assert.deepEqual(missing, [], "a knob a loader reads but doctor would flag as a typo");

  const stale = [...KNOWN_PLUGIN_KEYS].filter((key) => !read.has(key)).sort();
  assert.deepEqual(stale, [], "a knob doctor accepts that no loader reads any more");
});

test("a misspelled knob is caught, with the key it was probably meant to be", () => {
  const found = unknownPluginKeys({
    recallCompress: "auto",
    peerSorce: "git",
    RecallLimit: 10,
    claude_code: { autoRecal: false },
    codex: { recallPeerScope: "actor" },
  });

  assert.deepEqual(found.map((f) => f.key).sort(), [
    "plugin.RecallLimit",
    "plugin.claude_code.autoRecal",
    "plugin.peerSorce",
  ]);
  assert.equal(found.find((f) => f.key === "plugin.peerSorce").suggestion, "peerSource");
  assert.equal(found.find((f) => f.key === "plugin.RecallLimit").suggestion, "recallLimit");
  assert.equal(found.find((f) => f.key === "plugin.claude_code.autoRecal").suggestion, "autoRecall");
});

test("an empty or absent plugin section reports nothing", () => {
  for (const value of [undefined, null, {}, [], "text", 3]) {
    assert.deepEqual(unknownPluginKeys(value), [], `should be empty for ${JSON.stringify(value)}`);
  }
});
