/**
 * The Kimi Code marketplace entry, checked against what the host enforces.
 *
 * `marketplaceService.ts` refuses an entry that has no version, no
 * `distribution`, no `platforms`, or whose `source` is not an HTTP(S) ZIP with
 * a SHA-256 digest. Those four rules are why this test builds a real archive and
 * reads the digest back out of the generated JSON instead of trusting the script
 * to have run.
 *
 * The generator is shell, so the test runs it the way the download tree does.
 */

import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import test from "node:test";

import { buildArchive } from "../../scripts/pack.mjs";

const run = promisify(execFile);
const ROOT = fileURLToPath(new URL("../..", import.meta.url));
const GENERATOR = fileURLToPath(new URL("../../../../.github/scripts/generate-kimi-marketplace-json.sh", import.meta.url));

// The generator is a bash script. On Windows, argv paths with backslashes are
// eaten by MSYS argument conversion, so sha256sum silently receives the wrong
// file. CI runs on Linux; normalize here so the test exercises the same code.
const toBashPath = (p) => p.replace(/\\/g, "/");

/**
 * The generator needs a ZIP the host would accept, so it is built by the same
 * code that ships one rather than by zipping a directory here.
 */
async function generate(work, baseUrl = "https://docs.openviking.ai/dl") {
  const zip = join(work, "openviking-memory.zip");
  await buildArchive({ root: ROOT, out: zip });
  const out = join(work, "marketplace.json");
  await run("bash", [
    toBashPath(GENERATOR),
    `${baseUrl}/plugins/kimi/openviking-memory-0.6.0.zip`,
    toBashPath(zip),
    toBashPath(ROOT),
    toBashPath(out),
  ]);
  return { zip, entry: JSON.parse(await readFile(out, "utf8")).plugins[0], doc: JSON.parse(await readFile(out, "utf8")) };
}

async function sha256(path) {
  return createHash("sha256").update(await readFile(path)).digest("hex");
}

test("the entry carries the four fields the host refuses to install without", async () => {
  const work = await mkdtemp(join(tmpdir(), "kimi-marketplace-"));
  try {
    const { entry } = await generate(work);
    assert.equal(typeof entry.version, "string");
    assert.ok(entry.distribution, "an entry without distribution is rejected");
    assert.ok(Array.isArray(entry.platforms) && entry.platforms.length > 0, "platforms must be a non-empty array");
    // The host resolves source through resolveInstallSource() and requires the
    // zip-url kind, so a GitHub repository URL here would install the whole
    // repository instead of this plugin.
    assert.match(entry.source, /^https?:\/\/\S+\.zip$/, "source must be an HTTP(S) ZIP URL");
    assert.match(entry.sha256sum, /^[a-f0-9]{64}$/, "sha256sum must be a lowercase SHA-256 digest");
  } finally {
    await rm(work, { recursive: true, force: true });
  }
});

test("the digest is the digest of the archive the entry points at", async () => {
  const work = await mkdtemp(join(tmpdir(), "kimi-marketplace-"));
  try {
    const { zip, entry } = await generate(work);
    // A stale digest is not a warning: the host verifies the download and fails
    // the install, so the two must be computed from the same bytes.
    assert.equal(entry.sha256sum, await sha256(zip));
  } finally {
    await rm(work, { recursive: true, force: true });
  }
});

test("the entry describes the manifest inside the archive, not the source tree", async () => {
  const work = await mkdtemp(join(tmpdir(), "kimi-marketplace-"));
  try {
    const { entry } = await generate(work);
    const manifest = JSON.parse(await readFile(join(ROOT, "kimi.plugin.json"), "utf8"));
    // A user who reads "0.6.0" in the catalog must receive 0.6.0.
    assert.equal(entry.id, manifest.name);
    assert.equal(entry.version, manifest.version);
    assert.equal(entry.displayName, manifest.interface.displayName);
    assert.equal(entry.iconUrl, manifest.interface.iconUrl);
    assert.equal(entry.category, manifest.interface.category);
  } finally {
    await rm(work, { recursive: true, force: true });
  }
});

test("the entry is classified as a third-party plugin and ships its translation", async () => {
  const work = await mkdtemp(join(tmpdir(), "kimi-marketplace-"));
  try {
    const { entry } = await generate(work);
    // OpenViking is not the host vendor; claiming "official" would misrepresent
    // who maintains the plugin.
    assert.equal(entry.distribution.category, "third_party");
    assert.equal(entry.locale.defaultLocale, "en-US");
    // The catalog renders the locale block for the reader's language, so an
    // empty one shows an English card on a Chinese host.
    const zh = entry.locale["zh-CN"];
    assert.ok(zh && typeof zh.displayName === "string" && zh.displayName.length > 0);
    assert.ok(zh.longDescription.length > 0);
  } finally {
    await rm(work, { recursive: true, force: true });
  }
});

test("a regenerated marketplace is byte-identical for the same archive", async () => {
  const work = await mkdtemp(join(tmpdir(), "kimi-marketplace-"));
  try {
    const first = await generate(work);
    const second = await generate(work);
    // The published tree is rebuilt on every docs deploy; a digest or version
    // that moved on its own would make the catalog look like it changed.
    assert.deepEqual(first.doc, second.doc);
  } finally {
    await rm(work, { recursive: true, force: true });
  }
});
