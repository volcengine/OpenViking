import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const installer = join(ROOT, "examples", "memory-plugin-shared", "install.sh");
const stageScript = join(ROOT, ".github", "scripts", "stage-memory-plugin-marketplace.sh");

function run(command, args, options = {}) {
  return spawnSync(command, args, {
    cwd: ROOT,
    encoding: "utf8",
    ...options,
  });
}

test("release marketplace archive supports a ZCode TOS install", () => {
  const tmp = mkdtempSync(join(tmpdir(), "openviking-zcode-release-"));
  try {
    const stage = join(tmp, "memory-plugin-marketplace");
    const staged = run("bash", [stageScript, stage]);
    assert.equal(staged.status, 0, `${staged.stdout}\n${staged.stderr}`);

    const zipped = run("zip", ["-rq", join(tmp, "memory-plugin-marketplace.zip"), "memory-plugin-marketplace"], {
      cwd: tmp,
    });
    assert.equal(zipped.status, 0, `${zipped.stdout}\n${zipped.stderr}`);

    const home = join(tmp, "home");
    mkdirSync(home, { recursive: true });
    const installed = run("bash", [
      installer,
      "--harness", "zcode",
      "--dist", "tos",
      "--source", "archive",
      "--lang", "en",
      "--url", "http://127.0.0.1:1933",
      "--api-key", "",
      "--yes",
    ], {
      env: {
        ...process.env,
        HOME: home,
        OPENVIKING_HOME: join(home, ".openviking"),
        OPENVIKING_MARKETPLACE_ARCHIVE_URL: `file://${join(tmp, "memory-plugin-marketplace.zip")}`,
      },
    });
    assert.equal(installed.status, 0, `${installed.stdout}\n${installed.stderr}`);

    const integrationRoot = join(home, ".openviking", "agent-integrations", "zcode");
    assert.ok(existsSync(join(integrationRoot, "scripts", "zcode-hook.mjs")));
    assert.ok(existsSync(join(integrationRoot, "scripts", "zcode-capture.mjs")));
    assert.ok(existsSync(join(integrationRoot, "scripts", "shared", "async-writer.mjs")));

    const config = JSON.parse(readFileSync(join(home, ".zcode", "cli", "config.json"), "utf8"));
    assert.equal(config.hooks.enabled, true);
    assert.deepEqual(Object.keys(config.hooks.events), [
      "SessionStart",
      "UserPromptSubmit",
      "PreToolUse",
      "Stop",
    ]);
    assert.ok(config.mcp.servers.openviking);
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});

test("release marketplace archive retains the deprecated TRAE CLI integration for cleanup compatibility", () => {
  const tmp = mkdtempSync(join(tmpdir(), "openviking-trae-cli-release-"));
  try {
    const stage = join(tmp, "memory-plugin-marketplace");
    const staged = run("bash", [stageScript, stage]);
    assert.equal(staged.status, 0, `${staged.stdout}\n${staged.stderr}`);

    const zipped = run("zip", ["-rq", join(tmp, "memory-plugin-marketplace.zip"), "memory-plugin-marketplace"], {
      cwd: tmp,
    });
    assert.equal(zipped.status, 0, `${zipped.stdout}\n${zipped.stderr}`);

    const integrationRoot = join(stage, "trae-cli-memory-hooks");
    const manifest = JSON.parse(readFileSync(join(integrationRoot, "openviking.integration.json"), "utf8"));
    assert.equal(manifest.deprecated, true);
    assert.match(manifest.replacement, /codex-memory-plugin/u);
    assert.ok(existsSync(join(integrationRoot, "scripts", "trae-cli-hook.mjs")));
    assert.ok(existsSync(join(integrationRoot, "scripts", "uri-guard.mjs")));
    assert.ok(existsSync(join(integrationRoot, "servers", "mcp-proxy.mjs")));
  } finally {
    rmSync(tmp, { recursive: true, force: true });
  }
});
