import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("bundle has no runtime deps, exact-pinned dsh peers, and mounts an isolated OpenViking service", async () => {
  const manifest = JSON.parse(await readFile(
    new URL("./package.json", import.meta.url),
    "utf8",
  ));
  const patch = await readFile(new URL("./cordis.patch.yml", import.meta.url), "utf8");

  assert.equal(manifest.name, "@openviking/dsh-memory-plugin-traex");
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
  assert.match(patch, /name: '@openviking\/dsh-memory-plugin-traex'/);
});
