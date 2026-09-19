/**
 * One module writes the OpenViking request headers.
 *
 * Every harness used to spell the header block out for itself, and they
 * drifted: one named the operator to every proxy on the path whether or not the
 * server was trusted with it, one sent the api key twice, one read a different
 * field for the same account. `wire-headers.test.mjs` pins what goes out;
 * this pins that there is only one place left that can change it.
 *
 * `X-OpenViking-Actor-Peer` stands in for the whole block: it is the header a
 * hand-rolled stack cannot avoid writing, so a new one shows up here the day it
 * is added. Prose may name it freely — this reads source only.
 *
 * openclaw is deliberately out of scope; it does not share this stack yet.
 */

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { join, relative, sep } from "node:path";
import test from "node:test";

import { ASSEMBLED_ROOTS, ROOT, SHARED_DIR, TARGETS } from "./sync.mjs";

const PEER_HEADER = "X-OpenViking-Actor-Peer";
const BUILDER = join(SHARED_DIR, "ov-http.mjs");
const OPENCLAW = join(ROOT, "examples", "openclaw-plugin");

// Whoever the sync ships to is who this covers, so a new harness is guarded
// the day it is added rather than the day someone remembers this file.
const ROOTS = [...TARGETS.map((target) => target.root), ...ASSEMBLED_ROOTS, SHARED_DIR]
  .filter((root) => root !== OPENCLAW);

const SOURCE_EXTENSIONS = [".mjs", ".js", ".cjs", ".ts", ".mts"];
const SKIP_DIRS = new Set(["node_modules", ".git", "dist", "coverage"]);
// The vendored copies are byte copies of lib/, so they carry whatever lib/ does.
const VENDORED = TARGETS.map((target) => target.dir);

function isSource(name) {
  return SOURCE_EXTENSIONS.some((ext) => name.endsWith(ext)) && !/\.test\.[^.]+$/.test(name);
}

function sourceFiles(dir, out = []) {
  let entries;
  try {
    entries = readdirSync(dir, { withFileTypes: true });
  } catch {
    return out;
  }
  for (const entry of entries) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name) && !VENDORED.includes(path)) sourceFiles(path, out);
    } else if (isSource(entry.name)) {
      out.push(path);
    }
  }
  return out;
}

test("only the shared HTTP module writes the OpenViking request headers", () => {
  const files = ROOTS.flatMap((dir) => sourceFiles(dir));
  assert.ok(files.length > 100, `expected the plugin family's sources, found ${files.length}`);

  const offenders = files
    .filter((file) => file !== BUILDER && readFileSync(file, "utf-8").includes(PEER_HEADER))
    .map((file) => relative(ROOT, file).split(sep).join("/"));

  assert.deepEqual(
    offenders,
    [],
    `${PEER_HEADER} belongs to lib/ov-http.mjs; these build their own headers: ${offenders.join(", ")}`,
  );
});
