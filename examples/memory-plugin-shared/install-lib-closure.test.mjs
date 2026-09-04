/**
 * What the installer ships must equal what the shipped code imports.
 *
 * cursor, trae and trae-cli have no vendored copy of the shared runtime: the
 * installer assembles one by copying a hand-written list into
 * `$OV_HOME/agent-integrations/memory-plugin-shared/lib`. A module that list
 * forgets is an ERR_MODULE_NOT_FOUND on the first hook of a fresh install, and
 * one it carries that nothing imports is dead weight nobody notices. Both sides
 * are derived here so neither can drift.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const LIB = join(HERE, "lib");

/** The entrypoints cursor, trae and trae-cli import from the assembled lib. */
const ENTRYPOINTS = ["agent-hook-runtime.mjs", "agent-uri-guard.mjs", "mcp-proxy-core.mjs"];

const RELATIVE_IMPORT_RE = /(?:^|[\s;(])(?:import|export)\s[^;]*?from\s*["'](\.\/[^"']+)["']/g;
const DYNAMIC_IMPORT_RE = /\bimport\s*\(\s*["'](\.\/[^"']+)["']\s*\)/g;

function installedFiles() {
  const script = readFileSync(join(HERE, "install.sh"), "utf8");
  const block = /shared_dest\.tmp[\s\S]*?\n\s*for file in\s*((?:[^\n]*\\\n)*[^\n]*?);\s*do/.exec(script);
  assert.ok(block, "install.sh no longer has the `for file in ...; do` list of shared modules");
  return new Set(block[1].replace(/\\\n/g, " ").trim().split(/\s+/).filter(Boolean));
}

function importedFiles() {
  const seen = new Set();
  const pending = [...ENTRYPOINTS];
  while (pending.length) {
    const file = pending.pop();
    if (seen.has(file)) continue;
    seen.add(file);
    const source = readFileSync(join(LIB, file), "utf8");
    for (const re of [RELATIVE_IMPORT_RE, DYNAMIC_IMPORT_RE]) {
      for (const match of source.matchAll(re)) pending.push(match[1].slice(2));
    }
  }
  return seen;
}

test("the installer ships exactly the closure of what cursor and trae import", () => {
  const installed = installedFiles();
  const imported = importedFiles();

  for (const file of [...installed].sort()) {
    assert.ok(imported.has(file), `install.sh ships ${file}, which no assembled entrypoint imports`);
  }
  for (const file of [...imported].sort()) {
    assert.ok(installed.has(file), `${file} is imported but install.sh never copies it`);
  }
});
