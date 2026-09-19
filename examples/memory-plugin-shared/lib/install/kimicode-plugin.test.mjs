import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, mkdir, readFile, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(new URL("./kimicode-plugin.mjs", import.meta.url));

async function run(action, home, source, ...args) {
  return new Promise((resolve) => {
    execFile(process.execPath, [script, action, home, ...(source ? [source] : []), ...args], (error, stdout, stderr) => {
      resolve({ code: error?.code || 0, stdout, stderr });
    });
  });
}

test("installs an atomic managed copy and registry record, then purges only on request", async () => {
  const root = await mkdtemp(join(tmpdir(), "openviking-kimi-plugin-"));
  const source = join(root, "source");
  const home = join(root, "home");
  await writeFile(join(root, "setup"), "");
  await mkdir(source, { recursive: true });
  await writeFile(join(source, "kimi.plugin.json"), JSON.stringify({ name: "openviking-memory", version: "0.1.0" }));
  await writeFile(join(source, "marker.txt"), "managed");

  const installed = await run("install", home, source);
  assert.equal(installed.code, 0, installed.stderr);
  const record = JSON.parse(await readFile(join(home, "plugins", "installed.json"), "utf8"));
  assert.equal(record.plugins[0].root, join(home, "plugins", "managed", "openviking-memory"));
  assert.equal(existsSync(join(record.plugins[0].root, "marker.txt")), true);
  assert.equal((await run("verify", home)).code, 0);

  const removed = await run("remove", home);
  assert.equal(removed.code, 0, removed.stderr);
  assert.equal((await run("verify", home)).code, 1);
  assert.equal(existsSync(record.plugins[0].root), true);
  assert.equal((await run("remove", home, undefined, "--purge")).code, 0);
  assert.equal(existsSync(record.plugins[0].root), false);
});

test("serializes concurrent installs and removes without corrupting Kimi state", async () => {
  const root = await mkdtemp(join(tmpdir(), "openviking-kimi-plugin-concurrent-"));
  const source = join(root, "source");
  const home = join(root, "home");
  await mkdir(source, { recursive: true });
  await writeFile(join(source, "kimi.plugin.json"), JSON.stringify({ name: "openviking-memory", version: "0.1.0" }));
  await writeFile(join(source, "marker.txt"), "managed");

  const operations = [
    ...Array.from({ length: 8 }, () => run("install", home, source)),
    ...Array.from({ length: 4 }, () => run("remove", home, undefined, "--purge")),
  ];
  const results = await Promise.all(operations);
  for (const result of results) assert.equal(result.code, 0, result.stderr);

  const registryPath = join(home, "plugins", "installed.json");
  if (existsSync(registryPath)) {
    const registry = JSON.parse(await readFile(registryPath, "utf8"));
    assert.ok(Array.isArray(registry.plugins));
    assert.ok(registry.plugins.length <= 1);
    const record = registry.plugins.find((plugin) => plugin.id === "openviking-memory");
    assert.equal(existsSync(join(home, "plugins", "managed", "openviking-memory")), Boolean(record));
  }
  assert.equal(existsSync(join(home, "plugins", ".openviking-memory.lock")), false);
});
