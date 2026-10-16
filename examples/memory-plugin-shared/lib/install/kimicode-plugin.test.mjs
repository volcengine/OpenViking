import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { chmod, mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(new URL("./kimicode-plugin.mjs", import.meta.url));

function run(...args) {
  return new Promise((resolve) => {
    execFile(process.execPath, [script, ...args], (error, stdout, stderr) => {
      resolve({ code: error?.code || 0, stdout, stderr });
    });
  });
}

test("install preserves unrelated records and remove deletes only OpenViking", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-kimi-install-"));
  const source = join(root, "source");
  const home = join(root, "home");
  await mkdir(source, { recursive: true });
  await mkdir(join(home, "plugins"), { recursive: true });
  await writeFile(join(source, "kimi.plugin.json"), JSON.stringify({ name: "openviking-memory" }));
  await writeFile(join(source, "marker"), "new");
  await writeFile(join(home, "plugins", "installed.json"), JSON.stringify({
    version: 2,
    plugins: [
      { id: "someone-else", root: "/third-party", enabled: false },
      {
        id: "openviking-memory",
        root: "/stale-managed-copy",
        source: "local-path",
        originalSource: "/deleted-staging-bundle",
        enabled: false,
        installedAt: "2026-01-01T00:00:00.000Z",
      },
    ],
  }));

  const installed = await run("install", home, source);
  assert.equal(installed.code, 0, installed.stderr);
  let registry = JSON.parse(await readFile(join(home, "plugins", "installed.json"), "utf8"));
  assert.equal(registry.version, 2);
  assert.equal(registry.plugins.find((item) => item.id === "someone-else").enabled, false);
  const ours = registry.plugins.find((item) => item.id === "openviking-memory");
  assert.equal(ours.enabled, false);
  assert.equal(ours.installedAt, "2026-01-01T00:00:00.000Z");
  assert.equal("originalSource" in ours, false);
  assert.equal(existsSync(join(ours.root, "marker")), true);

  const removed = await run("remove", home);
  assert.equal(removed.code, 0, removed.stderr);
  registry = JSON.parse(await readFile(join(home, "plugins", "installed.json"), "utf8"));
  assert.deepEqual(registry.plugins.map((item) => item.id), ["someone-else"]);
  assert.equal(existsSync(ours.root), false);
});

test("invalid registry fails loudly without overwriting it", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-kimi-invalid-"));
  const source = join(root, "source");
  const home = join(root, "home");
  await mkdir(source, { recursive: true });
  await mkdir(join(home, "plugins"), { recursive: true });
  await writeFile(join(source, "kimi.plugin.json"), JSON.stringify({ name: "openviking-memory" }));
  const registry = join(home, "plugins", "installed.json");
  await writeFile(registry, "{invalid");
  const result = await run("install", home, source);
  assert.notEqual(result.code, 0);
  assert.equal(await readFile(registry, "utf8"), "{invalid");
});

test("a registry write failure restores the previous managed copy", async () => {
  const root = await mkdtemp(join(tmpdir(), "ov-kimi-rollback-"));
  const source = join(root, "source");
  const home = join(root, "home");
  const plugins = join(home, "plugins");
  const managed = join(plugins, "managed");
  const installed = join(managed, "openviking-memory");
  await mkdir(source, { recursive: true });
  await mkdir(installed, { recursive: true });
  await writeFile(join(source, "kimi.plugin.json"), JSON.stringify({ name: "openviking-memory" }));
  await writeFile(join(source, "marker"), "new");
  await writeFile(join(installed, "marker"), "old");
  const registry = join(plugins, "installed.json");
  const originalRegistry = JSON.stringify({
    version: 1,
    plugins: [{ id: "openviking-memory", root: installed, enabled: true }],
  });
  await writeFile(registry, originalRegistry);

  await chmod(plugins, 0o555);
  const result = await run("install", home, source);
  await chmod(plugins, 0o755);

  assert.notEqual(result.code, 0);
  assert.equal(await readFile(join(installed, "marker"), "utf8"), "old");
  assert.equal(await readFile(registry, "utf8"), originalRegistry);
});
