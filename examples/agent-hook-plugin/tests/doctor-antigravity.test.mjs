import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const repoPluginRoot = join(dirname(fileURLToPath(import.meta.url)), "..");
const repoSharedLib = join(repoPluginRoot, "..", "memory-plugin-shared", "lib");

function makeTestEnv(home) {
  const base = Object.fromEntries(
    Object.entries(process.env).filter(([key]) => !key.startsWith("OPENVIKING_")),
  );
  return { ...base, HOME: home, PATH: dirname(process.execPath) };
}

function setupInstalledAntigravity(home) {
  const integrationsRoot = join(home, ".openviking", "agent-integrations");
  const pluginRoot = join(integrationsRoot, "antigravity");
  const sharedLib = join(integrationsRoot, "memory-plugin-shared", "lib");
  mkdirSync(join(pluginRoot, "scripts"), { recursive: true });
  mkdirSync(join(pluginRoot, "hosts", "antigravity"), { recursive: true });
  mkdirSync(join(pluginRoot, "servers"), { recursive: true });
  mkdirSync(sharedLib, { recursive: true });

  cpSync(repoSharedLib, sharedLib, { recursive: true });
  for (const rel of [
    "plugin.json",
    "scripts/ov-memory-doctor.mjs",
    "scripts/hook.mjs",
    "scripts/uri-guard.mjs",
    "scripts/request-budget.mjs",
    "servers/mcp-proxy.mjs",
    "hosts/index.mjs",
    "hosts/antigravity/hooks.json",
    "hosts/antigravity/plugin.json",
  ]) {
    cpSync(join(repoPluginRoot, rel), join(pluginRoot, rel));
  }
  writeFileSync(
    join(pluginRoot, "integration.json"),
    JSON.stringify({ schemaVersion: 1, client: "antigravity", id: "openviking-memory" }),
  );
  writeFileSync(
    join(home, ".openviking", "ovcli.conf"),
    JSON.stringify({ url: "http://127.0.0.1:1933", account: "default", user: "default" }),
  );

  const agPluginDir = join(home, ".gemini", "config", "plugins", "openviking-memory");
  mkdirSync(agPluginDir, { recursive: true });
  const hookScript = join(pluginRoot, "scripts", "hook.mjs");
  // Quoted the way install.sh renders the command, apostrophes included.
  const quote = (value) => `'${String(value).replace(/'/g, `'"'"'`)}'`;
  const cmd = (event) => `OPENVIKING_INTEGRATION_ID='openviking-memory' OPENVIKING_HOOK_SOURCE='antigravity' ${quote(process.execPath)} ${quote(hookScript)} ${event} antigravity # openviking-memory`;
  writeFileSync(
    join(agPluginDir, "hooks.json"),
    JSON.stringify({
      "openviking-memory": {
        SessionStart: [{ type: "command", command: cmd("session-start"), timeout: 30 }],
        PreInvocation: [{ type: "command", command: cmd("pre-invocation"), timeout: 20 }],
        Stop: [{ type: "command", command: cmd("stop"), timeout: 30 }],
      },
    }),
  );
  writeFileSync(
    join(agPluginDir, "plugin.json"),
    JSON.stringify({ name: "openviking-memory", displayName: "OpenViking Memory" }),
  );

  return { pluginRoot, agPluginDir, hookScript };
}

function runDoctor(pluginRoot, home, args = []) {
  const doctor = join(pluginRoot, "scripts", "ov-memory-doctor.mjs");
  return spawnSync(process.execPath, [doctor, ...args, "--offline", "--json"], {
    cwd: home,
    env: makeTestEnv(home),
    encoding: "utf8",
  });
}

test("ov-memory-doctor recognizes antigravity from integration.json and tolerates manual MCP omission", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-doctor-ag-"));
  try {
    const { pluginRoot } = setupInstalledAntigravity(home);
    const run = runDoctor(pluginRoot, home);
    assert.equal(run.status, 0, run.stdout + run.stderr);
    const report = JSON.parse(run.stdout);
    assert.equal(report.harness, "antigravity");
    assert.equal(report.resolved.client, "antigravity");

    const install = report.sections.find((s) => s.title === "Plugin install");
    const hookFinding = install.findings.find((f) => f.title.includes("hooks in "));
    assert.equal(hookFinding?.level, "ok");
    assert.match(hookFinding?.title || "", /SessionStart, PreInvocation, Stop/);

    const mcpFinding = install.findings.find((f) => f.title.includes("mcp_config.json"));
    assert.equal(mcpFinding?.level, "info");
    assert.match(mcpFinding?.detail || "", /mcp_config\.json/);
    assert.doesNotMatch(mcpFinding?.detail || "", /--harness antigravity/);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("ov-memory-doctor accepts HTTP serverUrl MCP and warns when stdio MCP tags another client", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-doctor-ag-mcp-"));
  try {
    const { pluginRoot } = setupInstalledAntigravity(home);
    const mcpPath = join(home, ".gemini", "config", "mcp_config.json");
    writeFileSync(mcpPath, JSON.stringify({
      mcpServers: { openviking: { serverUrl: "http://127.0.0.1:1933/mcp" } },
    }));
    const httpRun = runDoctor(pluginRoot, home, ["antigravity"]);
    assert.equal(httpRun.status, 0, httpRun.stdout + httpRun.stderr);
    const httpInstall = JSON.parse(httpRun.stdout).sections.find((s) => s.title === "Plugin install");
    assert.ok(httpInstall.findings.some((f) => f.level === "ok" && f.title.includes("MCP server 'openviking'")));

    writeFileSync(mcpPath, JSON.stringify({
      mcpServers: {
        openviking: {
          command: "node",
          args: [join(pluginRoot, "servers", "mcp-proxy.mjs")],
          env: { OPENVIKING_HOOK_SOURCE: "claude_code" },
        },
      },
    }));
    const mismatchRun = runDoctor(pluginRoot, home, ["antigravity"]);
    assert.equal(mismatchRun.status, 0, mismatchRun.stdout + mismatchRun.stderr);
    const install = JSON.parse(mismatchRun.stdout).sections.find((s) => s.title === "Plugin install");
    assert.ok(install.findings.some((f) => f.level === "warn" && f.title.includes("tagged for claude_code")));
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("ov-memory-doctor validates OpenViking hook scripts, ignores foreign relative hooks, and counts lastStep", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-doctor-ag-activity-"));
  try {
    const { pluginRoot, agPluginDir } = setupInstalledAntigravity(home);
    const stateDir = join(home, ".openviking", "hook-state", "antigravity");
    mkdirSync(stateDir, { recursive: true });
    for (let i = 0; i < 5; i++) {
      writeFileSync(join(stateDir, `conv-${i}.json`), JSON.stringify({ version: 1, lastStep: i + 1 }));
    }
    const okRun = runDoctor(pluginRoot, home, ["antigravity"]);
    assert.equal(okRun.status, 0, okRun.stdout + okRun.stderr);
    const activity = JSON.parse(okRun.stdout).sections.find((s) => s.title === "Recent activity");
    assert.equal(
      activity.findings.some((f) => f.title.includes("no session has ever captured a turn")),
      false,
    );

    const missingScript = join(home, "missing", "scripts", "hook.mjs");
    writeFileSync(
      join(agPluginDir, "hooks.json"),
      JSON.stringify({
        "openviking-memory": {
          SessionStart: [{
            type: "command",
            command: `OPENVIKING_INTEGRATION_ID='openviking-memory' node ${missingScript} session-start antigravity`,
          }],
        },
      }),
    );
    const badRun = runDoctor(pluginRoot, home, ["antigravity"]);
    assert.equal(badRun.status, 1);
    const install = JSON.parse(badRun.stdout).sections.find((s) => s.title === "Plugin install");
    assert.ok(install.findings.some((f) => f.level === "fail" && f.detail === missingScript));

    // Foreign user hooks using relative paths in shared Cursor hooks.json must not fail OpenViking doctor.
    const cursorDir = join(home, ".cursor");
    mkdirSync(cursorDir, { recursive: true });
    writeFileSync(
      join(cursorDir, "hooks.json"),
      JSON.stringify({
        version: 1,
        hooks: {
          sessionStart: [
            { command: `OPENVIKING_INTEGRATION_ID='openviking-memory' '${process.execPath}' '${join(pluginRoot, "scripts", "hook.mjs")}' session-start cursor` },
            { command: "node ./.cursor/hooks/my-own-hook.mjs" },
          ],
        },
      }),
    );
    writeFileSync(join(cursorDir, "mcp.json"), JSON.stringify({ mcpServers: { openviking: { serverUrl: "http://127.0.0.1:1933/mcp" } } }));
    const cursorRun = runDoctor(pluginRoot, home, ["cursor"]);
    const cursorInstall = JSON.parse(cursorRun.stdout).sections.find((s) => s.title === "Plugin install");
    assert.equal(
      cursorInstall.findings.some((f) => f.title.includes("not on disk")),
      false,
    );
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("ov-memory-doctor reads a hook script path with an apostrophe the way the shell does", () => {
  const base = mkdtempSync(join(tmpdir(), "ov-doctor-ag-quote-"));
  const home = join(base, "o'brien");
  try {
    mkdirSync(home, { recursive: true });
    const { pluginRoot } = setupInstalledAntigravity(home);
    const run = runDoctor(pluginRoot, home, ["antigravity"]);
    assert.equal(run.status, 0, run.stdout + run.stderr);
    const install = JSON.parse(run.stdout).sections.find((s) => s.title === "Plugin install");
    assert.equal(install.findings.some((f) => f.title.includes("not on disk")), false);
  } finally {
    rmSync(base, { recursive: true, force: true });
  }
});

test("ov-memory-doctor takes the plugin's own mcp_config.json and warns about a global duplicate", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-doctor-ag-plugin-mcp-"));
  try {
    const { pluginRoot, agPluginDir } = setupInstalledAntigravity(home);
    writeFileSync(join(agPluginDir, "mcp_config.json"), JSON.stringify({
      mcpServers: {
        openviking: {
          command: process.execPath,
          args: [join(pluginRoot, "servers", "mcp-proxy.mjs")],
          env: { OPENVIKING_HOOK_SOURCE: "antigravity" },
        },
      },
    }));
    const pluginRun = runDoctor(pluginRoot, home, ["antigravity"]);
    assert.equal(pluginRun.status, 0, pluginRun.stdout + pluginRun.stderr);
    const pluginInstall = JSON.parse(pluginRun.stdout).sections.find((s) => s.title === "Plugin install");
    assert.ok(pluginInstall.findings.some((f) => f.level === "ok" && f.title.includes("plugins/openviking-memory/mcp_config.json")));
    assert.equal(pluginInstall.findings.some((f) => f.title.includes("not found")), false);

    writeFileSync(join(home, ".gemini", "config", "mcp_config.json"), JSON.stringify({
      mcpServers: { openviking: { serverUrl: "http://127.0.0.1:1933/mcp" } },
    }));
    const bothRun = runDoctor(pluginRoot, home, ["antigravity"]);
    const bothInstall = JSON.parse(bothRun.stdout).sections.find((s) => s.title === "Plugin install");
    assert.ok(bothInstall.findings.some((f) => f.level === "warn" && f.title.includes("configured in both")));
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});
