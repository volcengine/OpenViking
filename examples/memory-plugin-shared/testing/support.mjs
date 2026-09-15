/**
 * Helpers shared by the plugin test files.
 *
 * They live outside `lib/` on purpose: `sync.mjs` copies that directory into
 * every plugin, and nothing here belongs in a shipped plugin. Importing a
 * helper from a `*.test.mjs` file would also register that file's own tests a
 * second time in whichever runner picked it up.
 */

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { buildPluginConfig } from "../lib/plugin-config.mjs";

/**
 * A built config resolved against nothing at all, for callers that only need
 * the shape of the object a loader receives.
 */
export function buildConfigForTest(harness) {
  const dir = join(tmpdir(), "ov-plugin-config-absent");
  return buildPluginConfig(harness, {
    cwd: dir,
    env: {
      OPENVIKING_CLI_CONFIG_FILE: join(dir, "ovcli.conf"),
      OPENVIKING_CONFIG_FILE: join(dir, "ov.conf"),
      OPENVIKING_HOME: dir,
    },
  });
}

const PENDING_ENV_KEYS = [
  "OPENVIKING_PENDING_DIR",
  "OPENVIKING_PENDING_MAX_RETRIES",
  "OPENVIKING_PENDING_REPLAY_LIMIT",
  "OPENVIKING_PENDING_TTL_DAYS",
];

/**
 * Run `fn` against an empty pending queue in a throwaway directory.
 *
 * The queue reads its directory and its limits from the environment, so the
 * knobs are cleared for the duration and every one of them is restored after,
 * whether or not the test set it.
 */
export async function withPendingDir(fn) {
  const saved = PENDING_ENV_KEYS.map((key) => [key, process.env[key]]);
  const dir = await mkdtemp(join(tmpdir(), "openviking-pending-test-"));
  for (const key of PENDING_ENV_KEYS) delete process.env[key];
  process.env.OPENVIKING_PENDING_DIR = dir;
  try {
    return await fn(dir);
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    await rm(dir, { recursive: true, force: true });
  }
}

/** The JSON body of a request, or `null` when the request carried no body. */
export function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const raw = Buffer.concat(chunks).toString("utf-8");
      try {
        resolve(raw ? JSON.parse(raw) : null);
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

/** Answer with a JSON body, 200 unless the caller names another status. */
export function writeJson(res, value, statusCode = 200) {
  res.writeHead(statusCode, { "Content-Type": "application/json" });
  res.end(JSON.stringify(value));
}

/**
 * Run `fn` against a mock OpenViking listening on loopback.
 *
 * `handler` may be sync or async; whatever it throws becomes a 500 rather than
 * an unhandled rejection that outlives the test. `fn` receives the base URL and
 * a live log of every request the mock saw, so a test that only cares about
 * which endpoints were hit does not have to record them inside its handler.
 */
export async function withMockOpenViking(handler, fn) {
  const requests = [];
  const server = http.createServer((req, res) => {
    requests.push({
      method: req.method,
      path: new URL(req.url, "http://127.0.0.1").pathname,
      url: req.url,
      headers: req.headers,
    });
    Promise.resolve(handler(req, res)).catch((error) => {
      writeJson(res, { status: "error", error: String(error?.stack || error) }, 500);
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const { port } = server.address();
    return await fn(`http://127.0.0.1:${port}`, requests);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
}

/**
 * Run a hook script as its host runs it, and never reject.
 *
 * A hook that exits non-zero is a result a test may well be asserting on, so
 * the exit code comes back like any other output; call `expectExit` where a
 * clean exit is part of the expectation.
 */
export function runHookScript(scriptPath, { argv = [], input, env, cwd } = {}) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [scriptPath, ...argv], {
      cwd,
      env: { ...process.env, ...env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    let spawnError = null;
    let settled = false;
    const settle = (code, signal) => {
      if (settled) return;
      settled = true;
      resolve({ code, signal, stdout, stderr, error: spawnError });
    };
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (error) => { spawnError = error; settle(null, null); });
    child.on("close", settle);
    // A hook that answers without draining stdin makes this write fail EPIPE.
    child.stdin.on("error", () => {});
    child.stdin.end(
      input === undefined || typeof input === "string" ? (input ?? "") : JSON.stringify(input),
    );
  });
}

/** Assert a `runHookScript` result exited with `code`, and return the result. */
export function expectExit(result, code = 0) {
  const detail = result.stderr.trim() || String(result.error || "");
  assert.equal(result.code, code, detail || `expected exit ${code}, got ${result.code}`);
  return result;
}
