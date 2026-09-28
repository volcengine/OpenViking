import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { dirname, join, relative } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import semver from "semver";
import { PLUGIN_VERSION } from "./config.mjs";

const PLUGIN_DIR = dirname(fileURLToPath(import.meta.url));
const FORBIDDEN_IDENTIFIER = ["tra", "ex"].join("");
const FORBIDDEN_PATTERN = new RegExp(FORBIDDEN_IDENTIFIER, "i");

test("bundle uses neutral DSH naming and bounded peers", async () => {
  const manifest = JSON.parse(await readFile(
    new URL("./package.json", import.meta.url),
    "utf8",
  ));
  const patch = await readFile(new URL("./cordis.patch.yml", import.meta.url), "utf8");

  assert.equal(manifest.name, "@openviking/dsh-memory-plugin");
  assert.equal(manifest.dependencies, undefined);
  for (const [name, range] of Object.entries(manifest.peerDependencies)) {
    for (const version of ["0.1.0-rc.6", "0.1.5-rc.1", "0.1.5-rc.2", "0.1.5"]) {
      assert.ok(semver.satisfies(version, range), `${name} must accept ${version}`);
    }
    for (const version of ["0.0.1-rc.3", "0.1.0-rc.5", "0.1.5-alpha.2", "0.2.0-rc.1", "0.2.0"]) {
      assert.ok(!semver.satisfies(version, range), `${name} must reject ${version}`);
    }
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
  assert.doesNotMatch(patch, /group:/);
  assert.match(
    patch,
    /- id: openviking-memory-runtime\n\s+name: '@openviking\/dsh-memory-plugin'/,
  );
  assert.doesNotMatch(JSON.stringify(manifest), FORBIDDEN_PATTERN);
  assert.doesNotMatch(patch, FORBIDDEN_PATTERN);
});

test("the runtime and package lock report the published package version", async () => {
  const manifest = JSON.parse(await readFile(new URL("./package.json", import.meta.url), "utf8"));
  const lock = JSON.parse(await readFile(new URL("./package-lock.json", import.meta.url), "utf8"));
  assert.equal(PLUGIN_VERSION, manifest.version);
  assert.equal(lock.version, manifest.version);
  assert.equal(lock.packages[""].version, manifest.version);
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
