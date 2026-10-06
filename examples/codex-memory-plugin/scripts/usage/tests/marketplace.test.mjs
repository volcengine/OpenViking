import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const PLUGIN_DIR = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const ROOT = resolve(PLUGIN_DIR, "..", "..");

test("Codex marketplace exposes OV-Usage from the local plugin directory", async () => {
  const marketplace = JSON.parse(await readFile(join(ROOT, ".agents", "plugins", "marketplace.json"), "utf8"));
  const entry = marketplace.plugins.find((plugin) => plugin.name === "openviking-memory");
  assert.ok(entry);
  assert.equal(entry.source.source, "local");
  assert.equal(entry.source.path, "./examples/codex-memory-plugin");
  assert.equal(entry.policy.authentication, "ON_USE");
});

test("manifest and all hook commands resolve inside the package", async () => {
  const manifest = JSON.parse(await readFile(join(PLUGIN_DIR, ".codex-plugin", "plugin.json"), "utf8"));
  assert.equal(manifest.name, "openviking-memory");
  assert.equal(manifest.hooks, "./hooks/hooks.json");

  const config = JSON.parse(await readFile(join(PLUGIN_DIR, "hooks", "hooks.json"), "utf8"));
  for (const groups of Object.values(config.hooks)) {
    for (const group of groups) {
      for (const hook of group.hooks) {
        assert.match(hook.command, /^node "\$\{PLUGIN_ROOT\}\/scripts\/(?:usage\/)?[a-z-]+\.mjs"$/);
        const relative = hook.command.match(/scripts\/((?:usage\/)?[a-z-]+\.mjs)/)?.[0];
        await access(join(PLUGIN_DIR, relative));
      }
    }
  }
});
