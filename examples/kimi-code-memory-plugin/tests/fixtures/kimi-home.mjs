/**
 * Temporary Kimi Code homes, for tests that need the host's real layout.
 *
 * Every test gets its own `KIMI_CODE_HOME`, its own hook-state directory and
 * its own pending directory, so nothing here can touch a developer's sessions.
 */

import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

const KIMI_ENV = ["KIMI_CODE_HOME", "KIMI_PLUGIN_ROOT"];

/**
 * A fresh home directory, plus the env that points the plugin at it.
 *
 * The returned `restore()` puts the process environment back exactly as it was
 * and deletes the directory, so a failing assertion cannot leak state into the
 * next test.
 */
export async function withKimiHome(fn, { prefix = "kc-plugin-" } = {}) {
  const home = await mkdtemp(join(tmpdir(), prefix));
  const stateDir = join(home, "hook-state");
  const pendingDir = join(home, "pending");
  const saved = Object.fromEntries(KIMI_ENV.map((name) => [name, process.env[name]]));
  const savedState = process.env.OPENVIKING_HOOK_STATE_DIR;
  const savedPending = process.env.OPENVIKING_PENDING_DIR;
  const savedOvState = process.env.OPENVIKING_STATE_DIR;

  process.env.KIMI_CODE_HOME = home;
  process.env.OPENVIKING_HOOK_STATE_DIR = stateDir;
  process.env.OPENVIKING_PENDING_DIR = pendingDir;
  // The workspace-identity cache uses its own state dir. Without this, every
  // temporary cwd a test creates writes `ws-identity-*.json` into the real
  // `~/.openviking/state`, which is how that directory reached thousands of
  // files.
  process.env.OPENVIKING_STATE_DIR = join(home, "state");

  try {
    return await fn({ home, stateDir, pendingDir });
  } finally {
    for (const [name, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
    if (savedState === undefined) delete process.env.OPENVIKING_HOOK_STATE_DIR;
    else process.env.OPENVIKING_HOOK_STATE_DIR = savedState;
    if (savedPending === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = savedPending;
    if (savedOvState === undefined) delete process.env.OPENVIKING_STATE_DIR;
    else process.env.OPENVIKING_STATE_DIR = savedOvState;
    await rm(home, { recursive: true, force: true });
  }
}

/** Append records to `session_index.jsonl`. */
export async function appendIndex(home, rows) {
  const file = join(home, "session_index.jsonl");
  await mkdir(home, { recursive: true });
  await writeFile(file, rows.map((row) => `${JSON.stringify(row)}\n`).join(""), { flag: "a" });
}

/**
 * A session directory in the host's layout, with one wire per agent.
 *
 * `agents` maps an agent id to its records; each gets
 * `agents/<id>/wire.jsonl`. `state` is merged into the `state.json` the host
 * would have written.
 */
export async function writeSession(home, {
  sessionId,
  workspace = "workspace-a",
  agents = { main: [] },
  state = {},
  index = true,
  indexRows = undefined,
  workDir = "/work/project",
  wireTextFor = null,
} = {}) {
  const sessionDir = join(home, "sessions", workspace, sessionId);
  await mkdir(sessionDir, { recursive: true });
  const agentMeta = {};
  for (const [agentId, records] of Object.entries(agents)) {
    const dir = join(sessionDir, "agents", agentId);
    await mkdir(dir, { recursive: true });
    const body = wireTextFor ? wireTextFor(agentId, records) : records;
    await writeFile(join(dir, "wire.jsonl"), body, "utf8");
    agentMeta[agentId] = state.agents?.[agentId] ?? {
      type: agentId === "main" ? "main" : "sub",
      homedir: dir,
    };
  }
  const stateBody = {
    id: sessionId,
    version: 2,
    cwd: workDir,
    environmentId: "local",
    ...state,
    agents: { ...agentMeta, ...(state.agents || {}) },
  };
  await writeFile(join(sessionDir, "state.json"), JSON.stringify(stateBody, null, 2), "utf8");
  if (index) {
    await appendIndex(home, indexRows ?? [{ sessionId, sessionDir, workDir }]);
  }
  return { sessionDir, statePath: join(sessionDir, "state.json") };
}

/** Run a hook entry with a real stdin payload, as the host does. */
export async function runHook(script, payload, {
  cwd = undefined,
  env = {},
  timeoutMs = 30_000,
  argv = [],
} = {}) {
  const { spawn } = await import("node:child_process");
  const { fileURLToPath } = await import("node:url");
  const path = fileURLToPath(new URL(`../../scripts/${script}`, import.meta.url));
  return await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [path, ...argv], {
      cwd: cwd || process.cwd(),
      env: { ...process.env, ...env },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    const out = [];
    const err = [];
    child.stdout.on("data", (chunk) => out.push(chunk));
    child.stderr.on("data", (chunk) => err.push(chunk));
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error(`${script} timed out after ${timeoutMs}ms`));
    }, timeoutMs);
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({
        code,
        stdout: Buffer.concat(out).toString("utf8"),
        stderr: Buffer.concat(err).toString("utf8"),
      });
    });
    child.stdin.end(typeof payload === "string" ? payload : JSON.stringify(payload ?? {}));
  });
}

/** The payload shape the host sends, with the fields this plugin reads. */
export function hookPayload(event, {
  sessionId = "session_test",
  cwd = "/work/project",
  ...extra
} = {}) {
  return {
    hook_event_name: event,
    session_id: sessionId,
    cwd,
    client_type: "kimi-code",
    ...extra,
  };
}

/**
 * A temporary pending directory, for a test that needs the real queue.
 *
 * Separate from `withKimiHome` so a unit test can exercise a queue decision
 * without a whole fake host.
 */
export async function withPendingDirFor(fn) {
  const { mkdtemp, rm } = await import("node:fs/promises");
  const { tmpdir } = await import("node:os");
  const dir = await mkdtemp(join(tmpdir(), "kc-pending-"));
  const saved = process.env.OPENVIKING_PENDING_DIR;
  process.env.OPENVIKING_PENDING_DIR = dir;
  try {
    return await fn(dir);
  } finally {
    if (saved === undefined) delete process.env.OPENVIKING_PENDING_DIR;
    else process.env.OPENVIKING_PENDING_DIR = saved;
    await rm(dir, { recursive: true, force: true });
  }
}
