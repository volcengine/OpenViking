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

// The installer assembles the integration beside the shared runtime; copying
// both here keeps the doctor's `../memory-plugin-shared/lib` resolution intact.
function installAntigravity(home) {
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

  const pluginDir = join(home, ".gemini", "config", "plugins", "openviking-memory");
  mkdirSync(pluginDir, { recursive: true });
  const hookScript = join(pluginRoot, "scripts", "hook.mjs");
  const quote = (value) => `'${String(value).replace(/'/g, `'"'"'`)}'`;
  const cmd = (event) => `OPENVIKING_INTEGRATION_ID='openviking-memory' OPENVIKING_HOOK_SOURCE='antigravity' ${quote(process.execPath)} ${quote(hookScript)} ${event} antigravity # openviking-memory`;
  writeFileSync(
    join(pluginDir, "hooks.json"),
    JSON.stringify({
      "openviking-memory": {
        SessionStart: [{ type: "command", command: cmd("session-start"), timeout: 30 }],
        PreInvocation: [{ type: "command", command: cmd("pre-invocation"), timeout: 20 }],
        Stop: [{ type: "command", command: cmd("stop"), timeout: 30 }],
      },
    }),
  );
  writeFileSync(
    join(pluginDir, "mcp_config.json"),
    JSON.stringify({
      mcpServers: {
        openviking: {
          command: process.execPath,
          args: [join(pluginRoot, "servers", "mcp-proxy.mjs")],
          env: { OPENVIKING_HOOK_SOURCE: "antigravity" },
        },
      },
    }),
  );

  return { pluginRoot, pluginDir };
}

function runDoctor(pluginRoot, home) {
  return spawnSync(process.execPath, [join(pluginRoot, "scripts", "ov-memory-doctor.mjs"), "antigravity", "--offline", "--json", "--no-color"], {
    env: makeTestEnv(home),
    encoding: "utf8",
  });
}

const activityFindings = (run) => JSON.parse(run.stdout).sections.find((s) => s.title === "Recent activity").findings;
const neverCaptured = (run) => activityFindings(run).some((f) => f.title.includes("no session has ever captured a turn"));

test("Antigravity sessions that only advance lastStep are not reported as never capturing", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-doctor-ag-laststep-"));
  try {
    const { pluginRoot } = installAntigravity(home);
    const stateDir = join(home, ".openviking", "hook-state", "antigravity");
    mkdirSync(stateDir, { recursive: true });
    // Antigravity's adapter returns `{ ...state, lastStep }` and writes no
    // capturedHashes / capturedTurnIds; more than three such files used to
    // trip the "never captured" warning on every healthy install.
    for (const [i, lastStep] of [3, 7, 12, 20].entries()) {
      writeFileSync(join(stateDir, `conv-${i}.json`), JSON.stringify({ version: 1, lastStep }));
    }

    const run = runDoctor(pluginRoot, home);
    assert.equal(run.status, 0, run.stdout + run.stderr);
    assert.equal(neverCaptured(run), false, "a session with lastStep >= 0 has captured");
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("Antigravity sessions that never reached a step still report never capturing", () => {
  const home = mkdtempSync(join(tmpdir(), "ov-doctor-ag-laststep-minus-"));
  try {
    const { pluginRoot } = installAntigravity(home);
    const stateDir = join(home, ".openviking", "hook-state", "antigravity");
    mkdirSync(stateDir, { recursive: true });
    // -1 is the cursor the adapter starts from: the Stop hook ran, but no
    // step after it was ever capturable. The warning is the point here.
    for (let i = 0; i < 4; i++) {
      writeFileSync(join(stateDir, `conv-${i}.json`), JSON.stringify({ version: 1, lastStep: -1 }));
    }

    const run = runDoctor(pluginRoot, home);
    assert.equal(run.status, 0, run.stdout + run.stderr);
    assert.equal(neverCaptured(run), true);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});
