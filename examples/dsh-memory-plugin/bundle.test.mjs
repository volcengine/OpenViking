import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const PLUGIN_DIR = dirname(fileURLToPath(import.meta.url));
const FORBIDDEN_IDENTIFIER = ["tra", "ex"].join("");
const FORBIDDEN_PATTERN = new RegExp(FORBIDDEN_IDENTIFIER, "i");

test("bundle uses neutral DSH naming, exact-pinned peers, and an isolated service", async () => {
  const manifest = JSON.parse(await readFile(
    new URL("./package.json", import.meta.url),
    "utf8",
  ));
  const patch = await readFile(new URL("./cordis.patch.yml", import.meta.url), "utf8");

  assert.equal(manifest.name, "@openviking/dsh-memory-plugin");
  assert.equal(manifest.dependencies, undefined);
  // dsh constructors (defineTool / createUserMessage) come from peers the
  // installation heals at runtime; exact pins because dsh rc subpackages
  // have stale `latest` dist-tags. devDependencies mirror the pins so CI
  // tests exercise the same dsh surface a pin bump would ship.
  for (const [name, version] of Object.entries(manifest.peerDependencies)) {
    assert.match(version, /^\d+\.\d+\.\d+(-rc\.\d+)?$/, `${name} must be exact-pinned`);
    assert.equal(manifest.devDependencies[name], version, `${name} devDependency must mirror the peer pin`);
  }
  assert.ok(manifest.peerDependencies["@deepseek-ai/dsh-tools"]);
  assert.ok(manifest.peerDependencies["@deepseek-ai/dsh-llm"]);
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
