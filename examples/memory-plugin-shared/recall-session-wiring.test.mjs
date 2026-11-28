import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

// The shared request builder only sends `session_id` — and with it `dedup_turns`
// and server-side query expansion — when the caller supplies one. Every host
// runs the one flow, so there is one call site to pin: it must not silently
// fall back to stateless recall again.
const CALL_SITES = [
  {
    name: "the flow forwards the session's OpenViking id to recall",
    file: join(ROOT, "examples", "agent-hook-plugin", "lib", "flow", "prompt.mjs"),
    pattern: /recallDetailsForPrompt\([^)]*\{[^}]*\bsessionId\b/s,
  },
  // Under `actor` scope the effective peer is the only one asked, so dropping
  // the pre-git id here makes every memory written before it unreachable.
  {
    name: "recall forwards the pre-git peer for the actor-scope dual read",
    file: join(ROOT, "examples", "agent-hook-plugin", "lib", "agent-hook-runtime.mjs"),
    pattern: /legacyPeerId:\s*peer\.legacyPeerId/,
  },
];

for (const { name, file, pattern } of CALL_SITES) {
  test(name, async () => {
    const source = await readFile(file, "utf-8");
    assert.match(source, pattern);
  });
}

// The env var alone is not enough: buildContextSearchBody only emits
// `query_expansion` when the harness also marks it as configured. Both halves
// are declared once now — the knob in the schema, the flag in the one builder
// every loader calls — and `plugin-config.test.mjs` checks each harness gets it.
test("the query-expansion opt-out is declared once and reported for every harness", async () => {
  const { KNOB_BY_NAME } = await import("../agent-hook-plugin/lib/config-schema.mjs");
  const knob = KNOB_BY_NAME.get("recallQueryExpansion");
  assert.equal(knob.env, "OPENVIKING_RECALL_QUERY_EXPANSION");
  assert.deepEqual(knob.values, ["auto", "off"]);
  assert.equal(knob.sendOnlyWhenConfigured, true);

  const builder = join(ROOT, "examples", "agent-hook-plugin", "lib", "plugin-config.mjs");
  assert.match(await readFile(builder, "utf-8"), /`\$\{knob\.name\}Configured`/);
});
