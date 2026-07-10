import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";

const installer = join(dirname(fileURLToPath(import.meta.url)), "install.sh");

function writeJson(file, value) {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

function runInstall(home) {
  const result = spawnSync("bash", [installer,
    "--harness", "cursor,trae,trae-cn",
    "--source", "dev",
    "--lang", "en",
    "--url", "http://127.0.0.1:1933",
    "--api-key", "",
    "--yes",
  ], {
    cwd: resolve(dirname(installer), "..", ".."),
    env: { ...process.env, HOME: home, OPENVIKING_HOME: join(home, ".openviking") },
    encoding: "utf8",
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
}

function runUninstall(home) {
  const result = spawnSync("bash", [installer,
    "--harness", "cursor,trae,trae-cn",
    "--uninstall",
    "--yes",
  ], {
    cwd: resolve(dirname(installer), "..", ".."),
    env: { ...process.env, HOME: home, OPENVIKING_HOME: join(home, ".openviking") },
    encoding: "utf8",
  });
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
}

test("combined Cursor and TRAE install preserves unrelated hooks and is idempotent", () => {
  const home = mkdtempSync(join(tmpdir(), "openviking-agent-hooks-"));
  try {
    const cursorHooks = join(home, ".cursor", "hooks.json");
    const traeHooks = join(home, ".trae", "hooks.json");
    const traeCnHooks = join(home, ".trae-cn", "hooks.json");
    writeJson(cursorHooks, { version: 1, hooks: { stop: [{ command: "third-party stop" }] } });
    writeJson(traeHooks, { version: 1, hooks: { Stop: [
      { hooks: [{ type: "command", command: "third-party trae" }] },
      { hooks: [{ type: "command", command: "OPENVIKING_HOOK_SOURCE=trae node /tmp/openviking/claude-code-memory-plugin/scripts/trae-auto-capture.mjs" }] },
    ] } });
    writeJson(traeCnHooks, { version: 1, hooks: { Stop: [{ hooks: [{ type: "command", command: "third-party trae-cn" }] }] } });

    runInstall(home);
    runInstall(home);

    const cursor = JSON.parse(readFileSync(cursorHooks, "utf8"));
    assert.equal(cursor.hooks.stop.filter((entry) => entry.command.includes("cursor-hook.mjs")).length, 1);
    assert.ok(cursor.hooks.stop.some((entry) => entry.command === "third-party stop"));

    for (const [file, label] of [[traeHooks, "trae"], [traeCnHooks, "trae-cn"]]) {
      const config = JSON.parse(readFileSync(file, "utf8"));
      assert.equal(config.hooks.Stop.filter((entry) => JSON.stringify(entry).includes("trae-hook.mjs")).length, 1, label);
      assert.ok(config.hooks.Stop.some((entry) => JSON.stringify(entry).includes(`third-party ${label}`)), label);
      assert.equal(config.hooks.Stop.some((entry) => JSON.stringify(entry).includes("trae-auto-capture.mjs")), false, label);
    }

    assert.ok(JSON.parse(readFileSync(join(home, ".cursor", "mcp.json"), "utf8")).mcpServers.openviking);
    const traeMcp = process.platform === "darwin"
      ? join(home, "Library", "Application Support", "Trae", "User", "mcp.json")
      : join(home, ".trae", "mcp.json");
    const traeCnMcp = process.platform === "darwin"
      ? join(home, "Library", "Application Support", "Trae CN", "User", "mcp.json")
      : join(home, ".trae-cn", "mcp.json");
    assert.ok(JSON.parse(readFileSync(traeMcp, "utf8")).mcpServers.openviking);
    assert.ok(JSON.parse(readFileSync(traeCnMcp, "utf8")).mcpServers.openviking);

    runUninstall(home);
    assert.ok(JSON.parse(readFileSync(cursorHooks, "utf8")).hooks.stop.some((entry) => entry.command === "third-party stop"));
    assert.equal(JSON.parse(readFileSync(cursorHooks, "utf8")).hooks.stop.some((entry) => entry.command.includes("cursor-hook.mjs")), false);
    assert.equal(Boolean(JSON.parse(readFileSync(traeMcp, "utf8")).mcpServers.openviking), false);
    assert.equal(Boolean(JSON.parse(readFileSync(traeCnMcp, "utf8")).mcpServers.openviking), false);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});
