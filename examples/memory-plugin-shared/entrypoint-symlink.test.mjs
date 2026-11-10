/**
 * Scripts that double as importable modules run their CLI only when they are
 * the process entrypoint. Node resolves the entrypoint's `import.meta.url`
 * through symlinks while `process.argv[1]` keeps the path as typed, so a
 * symlinked `~/.openviking` or HOME made a lexical comparison fail and the
 * script exited 0 having done nothing. Each case starts one script through a
 * symlinked directory and checks for an effect only its CLI produces.
 */

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { ROOT, SKILL_TARGETS } from "./sync.mjs";
import { stripJsonc } from "./lib/install/jsonc-edit.mjs";

function withTemp(fn) {
  const dir = mkdtempSync(join(tmpdir(), "openviking-entrypoint-"));
  try {
    return fn(dir);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

/** Run `<link>/<name>` where `link` is a symlink to `realDir`. */
function runThroughLink(tmp, realDir, name, args = [], input = "") {
  const link = join(tmp, "link");
  symlinkSync(realDir, link, "dir");
  const env = { ...process.env, HOME: tmp };
  delete env.SOURCE_COMMIT;
  return spawnSync(process.execPath, [join(link, name), ...args], {
    input,
    env,
    encoding: "utf8",
    timeout: 20_000,
  });
}

const output = (result) => `status=${result.status}\n${result.stdout}\n${result.stderr}`;

test("host-json-config runs its CLI through a symlinked directory", () => {
  withTemp((tmp) => {
    const result = runThroughLink(tmp, join(ROOT, "examples/memory-plugin-shared/lib/install"), "host-json-config.mjs");
    assert.equal(result.status, 2, output(result));
    assert.match(result.stderr, /usage: host-json-config\.mjs/u);
  });
});

test("jsonc-edit runs its CLI through a symlinked directory", () => {
  withTemp((tmp) => {
    const config = join(tmp, "opencode.jsonc");
    const result = runThroughLink(
      tmp,
      join(ROOT, "examples/memory-plugin-shared/lib/install"),
      "jsonc-edit.mjs",
      [config, "", "/opt/openviking/servers/mcp-proxy.mjs"],
    );
    assert.equal(result.status, 0, output(result));
    const written = JSON.parse(stripJsonc(readFileSync(config, "utf8")));
    assert.deepEqual(written.mcp.openviking.command, ["node", "/opt/openviking/servers/mcp-proxy.mjs"]);
  });
});

for (const pluginDir of [
  "agent-plugins",
  "examples/agent-hook-plugin",
  "examples/claude-code-memory-plugin",
  "examples/codex-memory-plugin",
  "examples/dsh-memory-plugin",
  "examples/opencode-plugin",
]) {
  test(`${pluginDir} MCP proxy serves stdio through a symlinked directory`, () => {
    withTemp((tmp) => {
      const result = runThroughLink(tmp, join(ROOT, pluginDir, "servers"), "mcp-proxy.mjs", [], "not json\n");
      assert.equal(result.status, 0, output(result));
      assert.equal(JSON.parse(result.stdout.trim()).error.code, -32700, output(result));
    });
  });
}

// sync.mjs writes into the tree it sits in, so it runs from an empty copy of
// that tree rather than from this checkout.
test("sync runs through a symlinked directory", () => {
  withTemp((tmp) => {
    const tree = join(tmp, "tree");
    const shared = join(tree, "examples/memory-plugin-shared");
    mkdirSync(join(shared, "lib"), { recursive: true });
    copyFileSync(join(ROOT, "examples/memory-plugin-shared/sync.mjs"), join(shared, "sync.mjs"));
    for (const { skill } of SKILL_TARGETS) mkdirSync(join(tree, "examples/skills", skill), { recursive: true });
    const result = runThroughLink(tmp, tree, "examples/memory-plugin-shared/sync.mjs");
    assert.equal(result.status, 0, output(result));
    assert.ok(existsSync(join(shared, "lib/MANIFEST")), output(result));
  });
});

test("the pi experiment's credentials CLI runs through a symlinked directory", () => {
  withTemp((tmp) => {
    const result = runThroughLink(
      tmp,
      join(ROOT, "examples/pi-experimental-context-management/shared"),
      "credentials.mjs",
    );
    assert.equal(result.status, 2, output(result));
    assert.match(result.stderr, /usage: ov-credentials\.mjs/u);
  });
});

test("check-marketplace-archive runs through a symlinked directory", () => {
  withTemp((tmp) => {
    const result = runThroughLink(tmp, join(ROOT, ".github/scripts"), "check-marketplace-archive.mjs");
    assert.equal(result.status, 2, output(result));
    assert.match(result.stderr, /usage: check-marketplace-archive\.mjs/u);
  });
});

test("opencode-release-version runs through a symlinked directory", () => {
  withTemp((tmp) => {
    const result = runThroughLink(tmp, join(ROOT, ".github/scripts"), "opencode-release-version.mjs");
    assert.equal(result.status, 1, output(result));
    assert.match(result.stderr, /Missing source commit/u);
  });
});
