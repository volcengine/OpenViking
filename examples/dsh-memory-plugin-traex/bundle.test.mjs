import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("bundle is dependency-free and mounts an isolated OpenViking service", async () => {
  const manifest = JSON.parse(await readFile(
    new URL("./package.json", import.meta.url),
    "utf8",
  ));
  const patch = await readFile(new URL("./cordis.patch.yml", import.meta.url), "utf8");

  assert.equal(manifest.name, "@openviking/dsh-memory-plugin-traex");
  assert.equal(manifest.dependencies, undefined);
  assert.equal(manifest.peerDependencies, undefined);
  assert.equal(manifest.dsh.bundle.patch, "./cordis.patch.yml");
  assert.match(patch, /name: '@deepseek-ai\/cordis-plugin-group'/);
  assert.match(patch, /openvikingMemory: true/);
  assert.match(patch, /name: '@openviking\/dsh-memory-plugin-traex'/);
});
