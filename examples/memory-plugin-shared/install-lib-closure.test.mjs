/**
 * What the installer ships must equal what the shipped code imports.
 *
 * cursor, trae and zcode have no vendored copy of the shared runtime: the
 * installer assembles one in `$OV_HOME/agent-integrations/memory-plugin-shared/lib`
 * by copying the modules `lib/MANIFEST` names. A module the manifest forgets is
 * an ERR_MODULE_NOT_FOUND on the first hook of a fresh install, and one it
 * carries that nothing imports is dead weight nobody notices. The manifest is
 * generated from those sources by the same code that decides what the vendoring
 * targets ship, so it can only drift by not being regenerated — which is what
 * this asserts.
 */

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import test from "node:test";

import { MANIFEST_PATH, ROOT, assembledClosure, resolveTargets } from "./sync.mjs";

test("lib/MANIFEST holds the closure of what cursor, trae and zcode import", async () => {
  const manifest = readFileSync(MANIFEST_PATH, "utf8");
  assert.ok(manifest.endsWith("\n"), "the installer reads the manifest a line at a time");
  assert.deepEqual(
    manifest.split("\n").filter(Boolean),
    await assembledClosure(),
    "lib/MANIFEST is stale; run node examples/memory-plugin-shared/sync.mjs",
  );
});

// `lib/install/` is the installer's own code — the JSONC editor and the
// hooks/mcp merge — and no hook imports it. One `./install/...` import from a
// module that hooks do import would put it in the closure, and from there into
// the manifest and into every vendored copy: installer-time JavaScript shipped
// to every harness, out of reach of a reviewer who reads the diff as runtime.
test("the installer's own modules stay out of what the plugins ship", async () => {
  const installerOnly = readdirSync(join(dirname(MANIFEST_PATH), "install"))
    .filter((file) => file.endsWith(".mjs"));
  assert.ok(installerOnly.length > 0, "expected installer-only modules under lib/install");

  const shipped = [
    ...(await assembledClosure()).map((file) => ({ file, where: "lib/MANIFEST" })),
    ...(await resolveTargets()).flatMap((target) =>
      target.files.map((file) => ({ file, where: relative(ROOT, target.dir) }))),
  ];
  assert.deepEqual(
    shipped.filter((entry) => entry.file.startsWith("install/")),
    [],
    "a shipped module imports lib/install; the installer-only code must not be part of the runtime",
  );
});
