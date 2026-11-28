// The host table in both capability-reference pages is what host-table.mjs
// prints. The in-process hosts import their generated `shared/` copy of the
// library, so on a fresh checkout run `node examples/memory-plugin-shared/sync.mjs`
// first, as the CI does. Fix a failure by pasting the generator's output
// (`node examples/memory-plugin-shared/host-table.mjs --lang <lang>`) between the markers.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { hostTable } from "./host-table.mjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const BLOCK = /<!-- host-table:start -->\n([\s\S]*?)\n<!-- host-table:end -->/;

for (const lang of ["en", "zh"]) {
  test(`${lang} capability reference carries the generated host table`, async () => {
    const page = readFileSync(join(ROOT, "docs", lang, "agent-integrations", "16-capability-reference.md"), "utf8");
    const found = page.match(BLOCK);
    assert.ok(found, "host-table markers missing");
    assert.equal(found[1], await hostTable(lang));
  });
}
