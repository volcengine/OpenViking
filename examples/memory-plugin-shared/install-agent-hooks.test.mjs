import assert from "node:assert/strict";
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";

const installer = join(dirname(fileURLToPath(import.meta.url)), "install.sh");
const installedNode = spawnSync("bash", ["-c", "command -v node"], { encoding: "utf8" }).stdout.trim();

function writeJson(file, value) {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

function runInstaller(home, args) {
  return spawnSync("bash", [installer, ...args], {
    cwd: resolve(dirname(installer), "..", ".."),
    env: { ...process.env, HOME: home, OPENVIKING_HOME: join(home, ".openviking") },
    encoding: "utf8",
  });
}

function runInstall(home, harnesses = "cursor,trae,trae-cn") {
  const result = runInstaller(home, [
    "--harness", harnesses,
    "--source", "dev",
    "--lang", "en",
    "--url", "http://127.0.0.1:1933",
    "--api-key", "",
    "--yes",
  ]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
}

function runUninstall(home) {
  const result = runInstaller(home, [
    "--harness", "cursor,trae,trae-cn",
    "--uninstall",
    "--yes",
  ]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
}

test("combined Cursor and TRAE install preserves unrelated hooks and is idempotent", () => {
  const home = mkdtempSync(join(tmpdir(), "openviking-agent-hooks-"));
  try {
    const cursorHooks = join(home, ".cursor", "hooks.json");
    const cursorMcpPath = join(home, ".cursor", "mcp.json");
    const traeHooks = join(home, ".trae", "hooks.json");
    const traeCnHooks = join(home, ".trae-cn", "hooks.json");
    writeJson(cursorHooks, { version: 1, hooks: {
      stop: [{ command: "third-party stop" }],
      postToolUse: [{ command: "node /tmp/openviking/cursor-hook.mjs postToolUse # openviking-memory" }],
    } });
    writeJson(traeHooks, { version: 1, hooks: { Stop: [
      { hooks: [{ type: "command", command: "third-party trae" }] },
      { hooks: [{ type: "command", command: "OPENVIKING_HOOK_SOURCE=trae node /tmp/openviking/claude-code-memory-plugin/scripts/trae-auto-capture.mjs" }] },
    ] } });
    writeJson(traeCnHooks, { version: 1, hooks: { Stop: [{ hooks: [{ type: "command", command: "third-party trae-cn" }] }] } });
    writeJson(cursorMcpPath, { mcpServers: {
      "ov-mcp-server": { url: "https://example.com/mcp" },
      "third-party": { url: "https://third-party.example/mcp" },
    } });
    const traeCnMcp = process.platform === "darwin"
      ? join(home, "Library", "Application Support", "Trae CN", "User", "mcp.json")
      : join(home, ".trae-cn", "mcp.json");
    writeJson(traeCnMcp, { mcpServers: {
      "ov-mcp-server": { url: "https://api.vikingdb.cn-beijing.volces.com/openviking/mcp" },
      "third-party": { url: "http://127.0.0.1:3000/mcp" },
    } });

    runInstall(home);
    const firstInstallTimes = Object.fromEntries(["cursor", "trae", "trae-cn"].map((client) => {
      const manifest = JSON.parse(readFileSync(join(home, ".openviking", "agent-integrations", client, "integration.json"), "utf8"));
      return [client, { installedAt: manifest.installedAt, updatedAt: manifest.updatedAt }];
    }));
    runInstall(home);

    const cursor = JSON.parse(readFileSync(cursorHooks, "utf8"));
    assert.equal(cursor.hooks.stop.filter((entry) => entry.command.includes("auto-capture.mjs")).length, 1);
    assert.ok(cursor.hooks.stop.some((entry) => entry.command === "third-party stop"));
    assert.ok(cursor.hooks.stop.some((entry) => entry.command.includes(installedNode)));
    assert.ok(cursor.hooks.stop.some((entry) => entry.command.includes("OPENVIKING_INTEGRATION_ID='openviking-memory'")));
    assert.ok(cursor.hooks.stop.some((entry) => entry.command.includes("OPENVIKING_HOOK_SOURCE='cursor'")));
    assert.equal(cursor.hooks.beforeReadFile.filter((entry) => entry.command.includes("uri-guard.mjs")).length, 1);
    assert.equal(cursor.hooks.beforeShellExecution.filter((entry) => entry.command.includes("uri-guard.mjs")).length, 1);
    assert.equal(Boolean(cursor.hooks.postToolUse), false);

    for (const [file, label] of [[traeHooks, "trae"], [traeCnHooks, "trae-cn"]]) {
      const config = JSON.parse(readFileSync(file, "utf8"));
      assert.equal(config.hooks.Stop.filter((entry) => JSON.stringify(entry).includes("auto-capture.mjs")).length, 1, label);
      assert.ok(config.hooks.Stop.some((entry) => JSON.stringify(entry).includes(`third-party ${label}`)), label);
      assert.equal(config.hooks.Stop.some((entry) => JSON.stringify(entry).includes("trae-auto-capture.mjs")), false, label);
      assert.ok(config.hooks.Stop.some((entry) => JSON.stringify(entry).includes(`OPENVIKING_HOOK_SOURCE='${label}'`)), label);
      assert.equal(
        config.hooks.PreToolUse.filter((entry) => JSON.stringify(entry).includes("uri-guard.mjs")).length,
        1,
        label,
      );
    }

    const cursorServers = JSON.parse(readFileSync(cursorMcpPath, "utf8")).mcpServers;
    const cursorMcp = cursorServers.openviking;
    assert.equal(cursorMcp.command, installedNode);
    assert.equal(cursorMcp.env.OPENVIKING_INTEGRATION_ID, "openviking-memory");
    assert.equal(cursorMcp.env.OPENVIKING_HOOK_SOURCE, "cursor");
    assert.ok(cursorServers["ov-mcp-server"], "unknown legacy aliases must be preserved");
    assert.ok(cursorServers["third-party"]);
    assert.match(readFileSync(join(home, ".cursor", "rules", "openviking-memory.mdc"), "utf8"), /OpenViking/);
    assert.match(readFileSync(join(home, ".cursor", "skills", "openviking-memory", "SKILL.md"), "utf8"), /OpenViking Memory/);
    const shared = join(home, ".openviking", "agent-integrations", "memory-plugin-shared", "lib");
    assert.ok(existsSync(join(shared, "agent-hook-runtime.mjs")));
    assert.ok(existsSync(join(shared, "agent-uri-guard.mjs")));
    assert.ok(existsSync(join(shared, "batch-send.mjs")));
    assert.ok(existsSync(join(shared, "mcp-proxy-core.mjs")));
    assert.ok(existsSync(join(shared, "uri-guard.mjs")));
    for (const client of ["cursor", "trae", "trae-cn"]) {
      const manifest = JSON.parse(readFileSync(join(home, ".openviking", "agent-integrations", client, "integration.json"), "utf8"));
      assert.equal(manifest.id, "openviking-memory");
      assert.equal(manifest.client, client);
      assert.equal(manifest.installMode, "managed-native");
      assert.equal(manifest.source, "dev");
      assert.deepEqual(
        { installedAt: manifest.installedAt, updatedAt: manifest.updatedAt },
        firstInstallTimes[client],
        `${client} manifest must be idempotent`,
      );
    }
    for (const [client, args] of [
      ["cursor", []],
      ["trae", ["trae"]],
      ["trae-cn", ["trae-cn"]],
    ]) {
      const hook = join(home, ".openviking", "agent-integrations", client, "scripts", "session-start.mjs");
      const smoke = spawnSync(process.execPath, [hook, ...args], {
        env: { ...process.env, HOME: home, OPENVIKING_MEMORY_ENABLED: "0" },
        input: "{}",
        encoding: "utf8",
      });
      assert.equal(smoke.status, 0, `${client}: ${smoke.stderr}`);
    }
    for (const client of ["cursor", "trae", "trae-cn"]) {
      const guard = join(
        home,
        ".openviking",
        "agent-integrations",
        client,
        "scripts",
        "uri-guard.mjs",
      );
      const input = client === "cursor"
        ? { file_path: "viking://resources/project/file.md" }
        : { tool_name: "Read", tool_input: { file_path: "viking://resources/project/file.md" } };
      const guarded = spawnSync(process.execPath, [guard], {
        env: { ...process.env, HOME: home },
        input: JSON.stringify(input),
        encoding: "utf8",
      });
      assert.equal(guarded.status, 0, `${client}: ${guarded.stderr}`);
      assert.match(guarded.stdout, /deny/, `${client}: ${guarded.stderr}`);
    }
    const traeMcp = process.platform === "darwin"
      ? join(home, "Library", "Application Support", "Trae", "User", "mcp.json")
      : join(home, ".trae", "mcp.json");
    assert.ok(JSON.parse(readFileSync(traeMcp, "utf8")).mcpServers.openviking);
    assert.equal(JSON.parse(readFileSync(traeMcp, "utf8")).mcpServers.openviking.env.OPENVIKING_HOOK_SOURCE, "trae");
    const traeCnServers = JSON.parse(readFileSync(traeCnMcp, "utf8")).mcpServers;
    assert.ok(traeCnServers.openviking);
    assert.equal(traeCnServers.openviking.env.OPENVIKING_HOOK_SOURCE, "trae-cn");
    assert.ok(traeCnServers["third-party"]);
    assert.equal(Boolean(traeCnServers["ov-mcp-server"]), false);

    runUninstall(home);
    assert.ok(JSON.parse(readFileSync(cursorHooks, "utf8")).hooks.stop.some((entry) => entry.command === "third-party stop"));
    assert.equal(JSON.parse(readFileSync(cursorHooks, "utf8")).hooks.stop.some((entry) => entry.command.includes("auto-capture.mjs")), false);
    const cursorServersAfterUninstall = JSON.parse(readFileSync(cursorMcpPath, "utf8")).mcpServers;
    assert.ok(cursorServersAfterUninstall["ov-mcp-server"]);
    assert.ok(cursorServersAfterUninstall["third-party"]);
    assert.equal(Boolean(cursorServersAfterUninstall.openviking), false);
    assert.equal(existsSync(join(home, ".cursor", "rules", "openviking-memory.mdc")), false);
    assert.equal(existsSync(join(home, ".cursor", "skills", "openviking-memory")), false);
    assert.equal(Boolean(JSON.parse(readFileSync(traeMcp, "utf8")).mcpServers.openviking), false);
    assert.equal(Boolean(JSON.parse(readFileSync(traeCnMcp, "utf8")).mcpServers.openviking), false);
    assert.ok(JSON.parse(readFileSync(traeCnMcp, "utf8")).mcpServers["third-party"]);
    assert.equal(existsSync(join(home, ".openviking", "agent-integrations", "memory-plugin-shared")), false);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("Cursor-only install does not clean Claude or Codex shell configuration", () => {
  const home = mkdtempSync(join(tmpdir(), "openviking-agent-scope-"));
  try {
    const rc = join(home, ".zshrc");
    writeFileSync(rc, [
      "before",
      "# >>> openviking claude-code memory plugin >>>",
      "legacy claude",
      "# <<< openviking claude-code memory plugin <<<",
      "# >>> openviking-codex-plugin >>>",
      "legacy codex",
      "# <<< openviking-codex-plugin <<<",
      "after",
      "",
    ].join("\n"));
    runInstall(home, "cursor");
    assert.match(readFileSync(rc, "utf8"), /legacy claude/);
    assert.match(readFileSync(rc, "utf8"), /legacy codex/);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("malformed existing agent JSON fails without overwriting user configuration", () => {
  const home = mkdtempSync(join(tmpdir(), "openviking-agent-invalid-json-"));
  try {
    const hooks = join(home, ".cursor", "hooks.json");
    mkdirSync(dirname(hooks), { recursive: true });
    const original = '{"hooks":{"stop":[{"command":"third-party"}]},}';
    writeFileSync(hooks, original);
    const result = runInstaller(home, [
      "--harness", "cursor",
      "--source", "dev",
      "--lang", "en",
      "--url", "http://127.0.0.1:1933",
      "--api-key", "",
      "--yes",
    ]);
    assert.notEqual(result.status, 0, `${result.stdout}\n${result.stderr}`);
    assert.equal(readFileSync(hooks, "utf8"), original);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});
