import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { HARNESS_KEYS, KNOBS, KNOB_BY_KEY, WORKSPACE_KNOB_MAP, resolveKnobs } from "./lib/config-schema.mjs";
import { KNOWN_PLUGIN_KEYS, unknownPluginKeys } from "./lib/doctor-core.mjs";
import { buildConfigForTest } from "./testing/support.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

// Every knob reaches a loader through the object `buildPluginConfig` returns,
// and the modules below are the ones that still read knobs off the settings
// they were resolved from: a `settings.<key>` here is a key the schema has to
// declare, and a key the doctor therefore has to accept inside `plugin`.
const KNOB_READERS = [
  "examples/memory-plugin-shared/lib/plugin-config.mjs",
  "examples/claude-code-memory-plugin/scripts/config.mjs",
  "examples/codex-memory-plugin/scripts/config.mjs",
  "examples/opencode-plugin/lib/config.mjs",
  "examples/dsh-memory-plugin/config.mjs",
  "examples/pi-coding-agent-extension/config.ts",
  "examples/memory-plugin-shared/lib/agent-hook-runtime.mjs",
];

test("every knob a loader reads is declared in the schema", async () => {
  const built = buildConfigForTest("codex");
  for (const knob of KNOBS) {
    assert.ok(knob.name in built, `${knob.name} never reaches a loader`);
  }

  const read = new Set();
  for (const file of KNOB_READERS) {
    const source = await readFile(join(ROOT, file), "utf-8");
    for (const match of source.matchAll(/\bsettings\.([a-zA-Z_][a-zA-Z0-9_]*)/g)) read.add(match[1]);
  }
  const undeclared = [...read].filter((key) => !KNOB_BY_KEY.has(key)).sort();
  assert.deepEqual(undeclared, [], "a loader reads a key the schema never declares");
});

test("the doctor's known-knob list is the schema's own key list", () => {
  const declared = new Set(KNOB_BY_KEY.keys());
  assert.deepEqual([...KNOWN_PLUGIN_KEYS].sort(), [...declared].sort());
});

// Marking a knob `sendOnlyWhenConfigured` is a wire-protocol decision: the
// request omits the field so the server's default stands. The set therefore has
// to match the fields `recall-core` actually gates, in both directions.
test("exactly the knobs recall-core gates are marked send-only-when-configured", async () => {
  const marked = KNOBS.filter((knob) => knob.sendOnlyWhenConfigured).map((knob) => knob.name);
  assert.deepEqual(marked.sort(), [
    "recallCompressMaxBullets",
    "recallLimit",
    "recallMaxTokens",
    "recallQueryExpansion",
  ]);

  const source = await readFile(join(ROOT, "examples/memory-plugin-shared/lib/recall-core.mjs"), "utf-8");
  const gated = new Set();
  for (const match of source.matchAll(/\bcfg\.([a-zA-Z0-9_]+)Configured\b/g)) gated.add(match[1]);
  assert.deepEqual([...gated].sort(), marked.sort());
});

test("the schema is internally consistent", () => {
  const names = new Set();
  const envVars = new Map();
  for (const knob of KNOBS) {
    assert.ok(knob.name, "every knob needs a name");
    assert.ok(!names.has(knob.name), `duplicate knob ${knob.name}`);
    names.add(knob.name);
    assert.ok(knob.capability, `${knob.name} needs a capability`);
    if (knob.type === "enum") assert.ok(Array.isArray(knob.values), `${knob.name} needs its enum members`);
    if (knob.env) {
      // Two knobs sharing an environment variable would make one of them
      // unreachable, and which one depends on declaration order.
      assert.ok(!envVars.has(knob.env), `${knob.env} is claimed by ${envVars.get(knob.env)} and ${knob.name}`);
      envVars.set(knob.env, knob.name);
      assert.match(knob.env, /^OPENVIKING_/, `${knob.name}'s env var needs the shared prefix`);
    }
    for (const alias of knob.aliases || []) {
      assert.ok(!names.has(alias), `${alias} is both a knob and an alias`);
    }
  }

  // The workspace file names knobs by dotted path; a path pointing at nothing
  // would be a setting a repository could write that no loader ever reads.
  for (const [path, name] of Object.entries(WORKSPACE_KNOB_MAP)) {
    assert.ok(names.has(name), `${path} maps to unknown knob ${name}`);
  }
});

// A file may carry both spellings — a plugin that renamed a knob leaves the old
// one behind — and which one wins has to be the newer, not whichever the loop
// happened to reach last.
test("the canonical spelling wins over its own alias in the same layer", () => {
  const { settings } = resolveKnobs({
    layers: [{
      name: "file",
      data: {
        autoCapture: true,
        syncTurns: false,
        recallCompressThinking: "high",
        recallCompressReasoningEffort: "low",
        bypassSessionPatterns: ["/keep"],
        bypassPatterns: ["/stale"],
      },
    }],
  });
  assert.equal(settings.autoCapture, true);
  assert.equal(settings.recallCompressThinking, "high");
  assert.deepEqual(settings.bypassSessionPatterns, ["/keep"]);
});

test("a misspelled knob is caught, with the key it was probably meant to be", () => {
  const found = unknownPluginKeys({
    recallCompress: "auto",
    peerSorce: "git",
    RecallLimit: 10,
    claude_code: { autoRecal: false },
    codex: { recallPeerScope: "actor" },
    opencode: { captureMode: "keyword" },
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

test("every harness gets a per-harness override, under either spelling", () => {
  const overrides = Object.fromEntries(
    Object.values(HARNESS_KEYS).map((key) => [key, { recallLimit: 3 }]),
  );
  overrides["trae-cn"] = { recallLimit: 3 };
  assert.deepEqual(unknownPluginKeys(overrides), []);
});

test("an empty or absent plugin section reports nothing", () => {
  for (const value of [undefined, null, {}, [], "text", 3]) {
    assert.deepEqual(unknownPluginKeys(value), [], `should be empty for ${JSON.stringify(value)}`);
  }
});
