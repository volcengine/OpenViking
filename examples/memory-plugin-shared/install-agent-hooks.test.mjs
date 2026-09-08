import assert from "node:assert/strict";
import { chmodSync, cpSync, existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";
import test from "node:test";

// `--source dev` installs out of the checkout the installer sits in, and the
// rest of the suite reads that tree while these installs run, so the installs
// here get a copy of their own. The `.git` marker is a file, the way a linked
// worktree keeps it: enough for the installer to recognise a checkout, not
// enough for it to re-run the marketplace tests, which read repo-root files
// this copy does not carry.
const checkout = mkdtempSync(join(tmpdir(), "openviking-checkout-"));
process.on("exit", () => rmSync(checkout, { recursive: true, force: true }));
cpSync(resolve(dirname(fileURLToPath(import.meta.url)), ".."), join(checkout, "examples"), {
  recursive: true,
  filter: (source) => basename(source) !== "node_modules",
});
writeFileSync(join(checkout, ".git"), "gitdir: .\n");

const installer = join(checkout, "examples", "memory-plugin-shared", "install.sh");
const installedNode = spawnSync("bash", ["-c", "command -v node"], { encoding: "utf8" }).stdout.trim();

/** Every `command` string a hooks configuration holds, at any depth. */
function hookCommands(value, out = []) {
  if (Array.isArray(value)) for (const item of value) hookCommands(item, out);
  else if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      if (key === "command" && typeof child === "string") out.push(child);
      else hookCommands(child, out);
    }
  }
  return out;
}

function writeJson(file, value) {
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

function runInstaller(home, args, extraEnv = {}, script = installer) {
  return spawnSync("bash", [script, ...args], {
    cwd: checkout,
    env: {
      ...process.env,
      HOME: home,
      OPENVIKING_HOME: join(home, ".openviking"),
      ...extraEnv,
    },
    encoding: "utf8",
  });
}

function runInstall(home, harnesses = "cursor,trae,trae-cn,zcode") {
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

function runUninstall(home, harnesses = "cursor,trae,trae-cn,zcode") {
  const result = runInstaller(home, [
    "--harness", harnesses,
    "--uninstall",
    "--yes",
  ]);
  assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
}

test("TraeCode CLI 2.0 installs the Codex plugin alias and removes the deprecated integration", () => {
  const home = mkdtempSync(join(tmpdir(), "openviking-trae-cli-hooks-"));
  try {
    const binDir = join(home, "bin");
    const cliLog = join(home, "trae-cli.log");
    mkdirSync(binDir, { recursive: true });
    const cliPath = join(binDir, "trae-cli");
    writeFileSync(cliPath, `#!/bin/sh
printf '%s\n' "$*" >> "$TRAE_CLI_TEST_LOG"
case "$*" in
  "plugin marketplace list --json") printf '{"marketplaces":[]}\n' ;;
  "plugin marketplace list") printf 'Marketplaces:\n' ;;
  "plugin list") printf 'openviking-memory\n' ;;
  "plugin add "*) exit 2 ;;
esac
exit 0
`);
    chmodSync(cliPath, 0o755);

    const hooksPath = join(home, ".trae", "cli", "hooks.json");
    const configPath = join(home, ".trae", "traecli.toml");
    writeJson(hooksPath, {
      version: 1,
      hooks: {
        Stop: [
          { hooks: [{ type: "command", command: "third-party stop" }] },
          {
            hooks: [{
              type: "command",
              command: "OPENVIKING_INTEGRATION_ID=openviking-memory node /tmp/agent-integrations/trae-cli/scripts/auto-capture.mjs",
            }],
          },
        ],
      },
    });
    mkdirSync(dirname(configPath), { recursive: true });
    writeFileSync(configPath, [
      'model = "test-model"',
      "",
      "[mcp_servers.third_party]",
      'url = "https://example.com/mcp"',
      "",
      '[mcp_servers."openviking-memory"]',
      'command = "node"',
      'args = ["/tmp/agent-integrations/trae-cli/servers/mcp-proxy.mjs"]',
      "",
    ].join("\n"));
    const integrationRoot = join(home, ".openviking", "agent-integrations", "trae-cli");
    mkdirSync(integrationRoot, { recursive: true });
    writeFileSync(join(integrationRoot, "integration.json"), "{}\n");
    const sharedRoot = join(home, ".openviking", "agent-integrations", "memory-plugin-shared");
    mkdirSync(sharedRoot, { recursive: true });

    const installed = runInstaller(home, [
      "--harness", "trae-cli",
      "--source", "dev",
      "--lang", "en",
      "--url", "http://127.0.0.1:1933",
      "--api-key", "",
      "--yes",
    ], {
      PATH: `${binDir}:${dirname(installedNode)}:/usr/bin:/bin`,
      TRAE_CLI_TEST_LOG: cliLog,
    });
    assert.equal(installed.status, 0, `${installed.stdout}\n${installed.stderr}`);
    assert.match(installed.stdout, /Selected harnesses: trae-cli/u);
    assert.doesNotMatch(installed.stdout, /Selected harnesses: codex/u);
    assert.match(installed.stdout, /TraeCode CLI 2.0/);
    assert.doesNotMatch(installed.stdout, /trae-cli harness is deprecated/);

    const hooks = JSON.parse(readFileSync(hooksPath, "utf8"));
    assert.ok(hooks.hooks.Stop.some((entry) => JSON.stringify(entry).includes("third-party stop")));
    assert.doesNotMatch(JSON.stringify(hooks), /openviking/i);

    const config = readFileSync(configPath, "utf8");
    assert.match(config, /model = "test-model"/);
    assert.match(config, /\[mcp_servers\.third_party\]/);
    assert.doesNotMatch(config, /openviking-memory/);
    assert.equal(existsSync(integrationRoot), false);
    assert.equal(existsSync(sharedRoot), false);

    const commands = readFileSync(cliLog, "utf8");
    assert.match(commands, /plugin marketplace add .*\/examples/mu);
    assert.match(commands, /plugin install openviking-memory@openviking/mu);
    assert.match(commands, /plugin enable openviking-memory@openviking/mu);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("TraeCode CLI 2.0 keeps the deprecated integration when plugin installation fails", () => {
  const home = mkdtempSync(join(tmpdir(), "openviking-trae-cli-failed-migration-"));
  try {
    const binDir = join(home, "bin");
    mkdirSync(binDir, { recursive: true });
    const cliPath = join(binDir, "trae-cli");
    writeFileSync(cliPath, `#!/bin/sh
case "$*" in
  "plugin marketplace list --json") printf '{"marketplaces":[]}\n' ;;
  "plugin marketplace list") printf 'Marketplaces:\n' ;;
  "plugin add "*|"plugin install "*) exit 1 ;;
esac
exit 0
`);
    chmodSync(cliPath, 0o755);

    const hooksPath = join(home, ".trae", "cli", "hooks.json");
    const configPath = join(home, ".trae", "traecli.toml");
    writeJson(hooksPath, { hooks: { Stop: [{ hooks: [{
      type: "command",
      command: "OPENVIKING_INTEGRATION_ID=openviking-memory node /tmp/agent-integrations/trae-cli/scripts/auto-capture.mjs",
    }] }] } });
    mkdirSync(dirname(configPath), { recursive: true });
    writeFileSync(configPath, '[mcp_servers."openviking-memory"]\ncommand = "node"\n');
    const integrationRoot = join(home, ".openviking", "agent-integrations", "trae-cli");
    mkdirSync(integrationRoot, { recursive: true });

    const installed = runInstaller(home, [
      "--harness", "trae-cli",
      "--source", "dev",
      "--lang", "en",
      "--url", "http://127.0.0.1:1933",
      "--api-key", "",
      "--yes",
    ], { PATH: `${binDir}:${dirname(installedNode)}:/usr/bin:/bin` });

    assert.equal(installed.status, 0, `${installed.stdout}\n${installed.stderr}`);
    assert.match(installed.stdout, /plugin add\/install returned non-zero/u);
    assert.match(readFileSync(hooksPath, "utf8"), /openviking-memory/u);
    assert.match(readFileSync(configPath, "utf8"), /openviking-memory/u);
    assert.equal(existsSync(integrationRoot), true);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("TraeCode CLI 2.0 keeps the deprecated integration when plugin enable fails", () => {
  const home = mkdtempSync(join(tmpdir(), "openviking-trae-cli-failed-enable-"));
  try {
    const binDir = join(home, "bin");
    mkdirSync(binDir, { recursive: true });
    const cliPath = join(binDir, "trae-cli");
    writeFileSync(cliPath, `#!/bin/sh
case "$*" in
  "plugin marketplace list --json") printf '{"marketplaces":[]}\n' ;;
  "plugin marketplace list") printf 'Marketplaces:\n' ;;
  "plugin add "*) exit 2 ;;
  "plugin enable "*) exit 1 ;;
esac
exit 0
`);
    chmodSync(cliPath, 0o755);

    const hooksPath = join(home, ".trae", "cli", "hooks.json");
    const configPath = join(home, ".trae", "traecli.toml");
    writeJson(hooksPath, { hooks: { Stop: [{ hooks: [{
      type: "command",
      command: "OPENVIKING_INTEGRATION_ID=openviking-memory node /tmp/agent-integrations/trae-cli/scripts/auto-capture.mjs",
    }] }] } });
    mkdirSync(dirname(configPath), { recursive: true });
    writeFileSync(configPath, '[mcp_servers."openviking-memory"]\ncommand = "node"\n');
    const integrationRoot = join(home, ".openviking", "agent-integrations", "trae-cli");
    mkdirSync(integrationRoot, { recursive: true });

    const installed = runInstaller(home, [
      "--harness", "trae-cli",
      "--source", "dev",
      "--lang", "en",
      "--url", "http://127.0.0.1:1933",
      "--api-key", "",
      "--yes",
    ], { PATH: `${binDir}:${dirname(installedNode)}:/usr/bin:/bin` });

    assert.equal(installed.status, 0, `${installed.stdout}\n${installed.stderr}`);
    assert.match(installed.stdout, /could not be enabled/u);
    assert.match(readFileSync(hooksPath, "utf8"), /openviking-memory/u);
    assert.match(readFileSync(configPath, "utf8"), /openviking-memory/u);
    assert.equal(existsSync(integrationRoot), true);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("combined hook-host install preserves unrelated hooks and is idempotent", () => {
  const home = mkdtempSync(join(tmpdir(), "openviking-agent-hooks-"));
  try {
    const cursorHooks = join(home, ".cursor", "hooks.json");
    const cursorMcpPath = join(home, ".cursor", "mcp.json");
    const traeHooks = join(home, ".trae", "hooks.json");
    const traeCnHooks = join(home, ".trae-cn", "hooks.json");
    const zcodeConfig = join(home, ".zcode", "cli", "config.json");
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
    assert.equal(cursor.hooks.stop.filter((entry) => entry.command.includes("scripts/hook.mjs")).length, 1);
    assert.ok(cursor.hooks.stop.some((entry) => entry.command === "third-party stop"));
    assert.ok(cursor.hooks.stop.some((entry) => entry.command.includes(installedNode)));
    assert.ok(cursor.hooks.stop.some((entry) => entry.command.includes("OPENVIKING_INTEGRATION_ID='openviking-memory'")));
    assert.ok(cursor.hooks.stop.some((entry) => entry.command.includes("OPENVIKING_HOOK_SOURCE='cursor'")));
    assert.equal(cursor.hooks.beforeReadFile.filter((entry) => entry.command.includes("uri-guard.mjs")).length, 1);
    assert.equal(cursor.hooks.beforeShellExecution.filter((entry) => entry.command.includes("uri-guard.mjs")).length, 1);
    assert.equal(Boolean(cursor.hooks.postToolUse), false);

    for (const [file, label] of [[traeHooks, "trae"], [traeCnHooks, "trae-cn"]]) {
      const config = JSON.parse(readFileSync(file, "utf8"));
      assert.equal(config.hooks.Stop.filter((entry) => JSON.stringify(entry).includes("scripts/hook.mjs")).length, 1, label);
      assert.ok(config.hooks.Stop.some((entry) => JSON.stringify(entry).includes(`third-party ${label}`)), label);
      assert.equal(config.hooks.Stop.some((entry) => JSON.stringify(entry).includes("trae-auto-capture.mjs")), false, label);
      assert.ok(config.hooks.Stop.some((entry) => JSON.stringify(entry).includes(`OPENVIKING_HOOK_SOURCE='${label}'`)), label);
      assert.equal(
        config.hooks.PreToolUse.filter((entry) => JSON.stringify(entry).includes("uri-guard.mjs")).length,
        1,
        label,
      );
    }
    const zcodeEvents = JSON.parse(readFileSync(zcodeConfig, "utf8")).hooks.events;
    assert.equal(
      zcodeEvents.PreToolUse.filter((entry) => JSON.stringify(entry).includes("uri-guard.mjs")).length,
      1,
    );

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
    // A hooks.json entry that names a script the install did not put on disk
    // fails only when the host first runs it, so the rendered commands are
    // checked against the tree they were rendered for.
    for (const [file, label] of [[cursorHooks, "cursor"], [traeHooks, "trae"], [traeCnHooks, "trae-cn"]]) {
      const commands = hookCommands(JSON.parse(readFileSync(file, "utf8")))
        .filter((command) => command.includes("# openviking-memory"));
      assert.ok(commands.length > 0, `${label}: no OpenViking hook commands were installed`);
      for (const command of commands) {
        const script = /'([^']*\.mjs)'/u.exec(command)?.[1];
        assert.ok(script, `${label}: ${command} names no script`);
        assert.ok(existsSync(script), `${label}: ${script} is missing after install`);
      }
    }
    for (const [client, event] of [["cursor", "sessionStart"], ["trae", "session-start"], ["trae-cn", "session-start"]]) {
      const hook = join(home, ".openviking", "agent-integrations", client, "scripts", "hook.mjs");
      const smoke = spawnSync(process.execPath, [hook, event, client], {
        env: { ...process.env, HOME: home, OPENVIKING_MEMORY_ENABLED: "0" },
        input: "{}",
        encoding: "utf8",
      });
      assert.equal(smoke.status, 0, `${client}: ${smoke.stderr}`);
      assert.equal(smoke.stderr, "", client);
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
      const guarded = spawnSync(process.execPath, [guard, client], {
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
    assert.equal(JSON.parse(readFileSync(cursorHooks, "utf8")).hooks.stop.some((entry) => entry.command.includes("scripts/hook.mjs")), false);
    const cursorServersAfterUninstall = JSON.parse(readFileSync(cursorMcpPath, "utf8")).mcpServers;
    assert.ok(cursorServersAfterUninstall["ov-mcp-server"]);
    assert.ok(cursorServersAfterUninstall["third-party"]);
    assert.equal(Boolean(cursorServersAfterUninstall.openviking), false);
    assert.equal(existsSync(join(home, ".cursor", "rules", "openviking-memory.mdc")), false);
    assert.equal(existsSync(join(home, ".cursor", "skills", "openviking-memory")), false);
    assert.equal(Boolean(JSON.parse(readFileSync(traeMcp, "utf8")).mcpServers.openviking), false);
    assert.equal(Boolean(JSON.parse(readFileSync(traeCnMcp, "utf8")).mcpServers.openviking), false);
    assert.ok(JSON.parse(readFileSync(traeCnMcp, "utf8")).mcpServers["third-party"]);
    // Every event the installer wrote has to come back empty, the URI guard's
    // included: a surviving entry runs a script the uninstall just deleted.
    const cursorEventsAfter = JSON.parse(readFileSync(cursorHooks, "utf8")).hooks;
    for (const event of ["beforeReadFile", "beforeShellExecution"]) {
      assert.deepEqual(cursorEventsAfter[event] || [], [], event);
    }
    for (const [file, label] of [[traeHooks, "trae"], [traeCnHooks, "trae-cn"]]) {
      assert.deepEqual(JSON.parse(readFileSync(file, "utf8")).hooks.PreToolUse || [], [], label);
    }
    assert.deepEqual(
      JSON.parse(readFileSync(zcodeConfig, "utf8")).hooks?.events?.PreToolUse || [],
      [],
      "zcode",
    );
    // A backup taken while removing entries would keep a copy of them, so the
    // uninstall writes none and reclaims the one the install left.
    for (const file of [cursorHooks, cursorMcpPath, traeHooks, traeMcp, traeCnHooks, traeCnMcp]) {
      assert.equal(existsSync(`${file}.bak`), false, `${file}.bak`);
    }
    assert.equal(existsSync(join(home, ".openviking", "agent-integrations", "memory-plugin-shared")), false);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

// Installing the thin harnesses together lets one client's verification pass
// against a directory another client happened to create, so each is installed
// into a HOME of its own.
for (const client of ["cursor", "trae", "trae-cn", "zcode"]) {
  test(`${client} installs and verifies on its own`, () => {
    const home = mkdtempSync(join(tmpdir(), `openviking-solo-${client}-`));
    try {
      const result = runInstaller(home, [
        "--harness", client,
        "--source", "dev",
        "--lang", "en",
        "--url", "http://127.0.0.1:1933",
        "--api-key", "",
        "--yes",
      ]);
      assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
      assert.doesNotMatch(result.stdout, new RegExp(`^!!\\s+${client}:`, "mu"), result.stdout);
      assert.ok(existsSync(join(home, ".openviking", "agent-integrations", client, "scripts", "hook.mjs")));
    } finally {
      rmSync(home, { recursive: true, force: true });
    }
  });
}

// The documented uninstall pipes install.sh from a URL, so the running script
// has no lib/ sibling, and the first uninstall drops the assembled copy under
// $OV_HOME. Nothing left to read is not a reason to abort — and never a reason
// to fetch sources.
test("uninstall with no installer runtime on disk removes what it can and fetches nothing", () => {
  const home = mkdtempSync(join(tmpdir(), "openviking-uninstall-sourceless-"));
  try {
    runInstall(home, "cursor");
    const detached = join(home, "install.sh");
    cpSync(installer, detached);
    rmSync(join(home, ".openviking", "agent-integrations", "memory-plugin-shared"), {
      recursive: true,
      force: true,
    });
    const result = runInstaller(home, ["--harness", "cursor", "--uninstall", "--lang", "en", "--yes"], {
      OPENVIKING_REPO_URL: "file:///nonexistent/openviking.git",
      OPENVIKING_REPO_DIR: join(home, "openviking-repo"),
    }, detached);
    const output = `${result.stdout}\n${result.stderr}`;
    assert.equal(result.status, 0, output);
    assert.doesNotMatch(output, /Cloning|Refreshing checkout/u, output);
    assert.equal(existsSync(join(home, "openviking-repo")), false);
    assert.equal(existsSync(join(home, ".openviking", "agent-integrations", "cursor")), false);
    assert.equal(existsSync(join(home, ".cursor", "rules", "openviking-memory.mdc")), false);
    // The host's own files could not be edited, so the uninstall has to name them.
    assert.match(result.stdout, /by hand from:.*\.cursor\/hooks\.json/u, output);
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
