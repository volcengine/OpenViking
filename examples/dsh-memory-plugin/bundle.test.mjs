import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const PLUGIN_DIR = dirname(fileURLToPath(import.meta.url));
const FORBIDDEN_IDENTIFIER = ["tra", "ex"].join("");
const FORBIDDEN_PATTERN = new RegExp(FORBIDDEN_IDENTIFIER, "i");

test("bundle uses neutral DSH naming, bounded peers, and an isolated service", async () => {
  const manifest = JSON.parse(await readFile(
    new URL("./package.json", import.meta.url),
    "utf8",
  ));
  const patch = await readFile(new URL("./cordis.patch.yml", import.meta.url), "utf8");

  assert.equal(manifest.name, "@openviking/dsh-memory-plugin");
  assert.equal(manifest.dependencies, undefined);
  // DSH rc releases intentionally float their core packages within 0.1.x.
  // Accept that host range while keeping CI pinned to the oldest supported
  // contract so a newer local install cannot silently raise our minimum.
  const supportedPeerRange = ">=0.1.0-rc.6 <0.2.0";
  for (const [name, version] of Object.entries(manifest.peerDependencies)) {
    assert.equal(version, supportedPeerRange, `${name} must use the bounded 0.1.x range`);
    assert.equal(manifest.devDependencies[name], "0.1.0-rc.6", `${name} must test the minimum`);
  }
  assert.ok(manifest.peerDependencies["@deepseek-ai/dsh-mcp-client"]);
  assert.ok(manifest.peerDependencies["@deepseek-ai/dsh-llm"]);
  for (const [name, version] of Object.entries(manifest.peerDependencies)) {
    assert.equal(
      manifest.overrides[name],
      manifest.devDependencies[name],
      `${name} override must hold the transitive family at the tested minimum`,
    );
  }
  assert.equal(manifest.dsh.bundle.patch, "./cordis.patch.yml");
  assert.match(patch, /name: '@deepseek-ai\/cordis-plugin-group'/);
  assert.match(patch, /openvikingMemory: true/);
  assert.match(patch, /name: '@openviking\/dsh-memory-plugin'/);
  assert.doesNotMatch(JSON.stringify(manifest), FORBIDDEN_PATTERN);
  assert.doesNotMatch(patch, FORBIDDEN_PATTERN);
});

test("plugin source tree contains no product-specific identifier", async () => {
  for (const file of await sourceFiles(PLUGIN_DIR)) {
    const path = relative(PLUGIN_DIR, file);
    assert.doesNotMatch(path, FORBIDDEN_PATTERN);
    assert.doesNotMatch(await readFile(file, "utf8"), FORBIDDEN_PATTERN, path);
  }
});

async function sourceFiles(dir) {
  const files = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    if (entry.name === "node_modules") continue;
    const path = join(dir, entry.name);
    if (entry.isDirectory()) files.push(...await sourceFiles(path));
    else files.push(path);
  }
  return files;
}
