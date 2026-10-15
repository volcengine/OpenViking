import assert from "node:assert/strict";
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const BASE = mkdtempSync(join(tmpdir(), "ov-cache-heal-"));
process.env.OPENVIKING_CODEX_STATE_DIR = join(BASE, "state");

const { healRetiredVersions } = await import("./cache-heal.mjs");

const DAY = 24 * 60 * 60 * 1000;
const cache = join(BASE, "cache", "openviking", "openviking-memory");

function install(version) {
  const root = join(cache, version);
  mkdirSync(join(root, ".codex-plugin"), { recursive: true });
  writeFileSync(join(root, ".codex-plugin", "plugin.json"), JSON.stringify({ version }));
  return root;
}

const isLink = (path) => { try { return lstatSync(path).isSymbolicLink(); } catch { return false; } };
const record = () => JSON.parse(readFileSync(join(BASE, "state", "plugin-versions.json"), "utf-8"));

test("an upgrade links the removed version to the running one, and later upgrades follow", () => {
  const v1 = install("1.0.0");
  healRetiredVersions({ root: v1, now: 0 });

  // Codex installs 1.0.1 and deletes 1.0.0 while older sessions still point at it.
  rmSync(v1, { recursive: true });
  const v2 = install("1.0.1");
  healRetiredVersions({ root: v2, now: DAY });
  healRetiredVersions({ root: v2, now: DAY });
  assert.ok(isLink(v1));
  assert.equal(realpathSync(v1), realpathSync(v2));

  // The next upgrade deletes 1.0.1; both older paths now resolve to 1.0.2.
  rmSync(v2, { recursive: true });
  const v3 = install("1.0.2");
  healRetiredVersions({ root: v3, now: 2 * DAY });
  assert.equal(realpathSync(v1), realpathSync(v3));
  assert.equal(realpathSync(v2), realpathSync(v3));

  // A week after a version last ran, its link goes away.
  healRetiredVersions({ root: v3, now: 7.5 * DAY });
  assert.equal(existsSync(v1) || isLink(v1), false);
  assert.ok(isLink(v2));
  assert.deepEqual(Object.keys(record()).sort(), ["1.0.1", "1.0.2"]);
  rmSync(join(BASE, "cache"), { recursive: true });
  rmSync(join(BASE, "state"), { recursive: true });
});

test("a downgrade does not link the newer version", () => {
  const v2 = install("2.0.0");
  healRetiredVersions({ root: v2, now: 0 });
  rmSync(v2, { recursive: true });
  healRetiredVersions({ root: install("1.9.0"), now: 1 });
  assert.equal(existsSync(v2) || isLink(v2), false);
  rmSync(join(BASE, "cache"), { recursive: true });
  rmSync(join(BASE, "state"), { recursive: true });
});

test("a copy outside a versioned cache directory is left alone", () => {
  const root = join(BASE, "checkout", "codex-memory-plugin");
  mkdirSync(join(root, ".codex-plugin"), { recursive: true });
  writeFileSync(join(root, ".codex-plugin", "plugin.json"), JSON.stringify({ version: "1.0.0" }));
  healRetiredVersions({ root, now: 0 });
  assert.equal(existsSync(join(BASE, "state", "plugin-versions.json")), false);
});
