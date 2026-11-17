/**
 * The doctor, run the way a user runs it.
 *
 * It is a diagnostic, so the two things that matter are that it reports the
 * facts it can see and that `--offline` really makes no request. Both are
 * checked here against a temporary host, never the developer's own.
 */

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { utimes } from "node:fs/promises";
import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { startFakeOv } from "../fake-ov/server.mjs";
import { withKimiHome, writeSession } from "../fixtures/kimi-home.mjs";
import { metadata, simpleTurn, wireText } from "../fixtures/wire-fixtures.mjs";

const DOCTOR = fileURLToPath(new URL("../../scripts/doctor.mjs", import.meta.url));

/** The environment both the doctor child and an in-process config see. */
function doctorEnv(home, server) {
  return {
    ...process.env,
    KIMI_CODE_HOME: home,
    OPENVIKING_HOOK_STATE_DIR: join(home, "hook-state"),
    OPENVIKING_PENDING_DIR: join(home, "pending"),
    // Isolate the credential files too: a developer's broken ~/.openviking
    // would otherwise make this test fail for a reason that is not the code.
    HOME: home,
    USERPROFILE: home,
    OPENVIKING_CLI_CONFIG_FILE: join(home, ".openviking", "ovcli.conf"),
    OPENVIKING_CONFIG_FILE: join(home, ".openviking", "ov.conf"),
    OPENVIKING_URL: server ? server.url : "http://127.0.0.1:9",
    OPENVIKING_API_KEY: "sk-doctor",
    OPENVIKING_AUTH_MODE: "api_key",
    OPENVIKING_CREDENTIAL_SOURCE: "env",
  };
}

async function runDoctor(args, { home, server }) {
  return await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [DOCTOR, ...args], {
      env: doctorEnv(home, server),
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    const out = [];
    const err = [];
    child.stdout.on("data", (chunk) => out.push(chunk));
    child.stderr.on("data", (chunk) => err.push(chunk));
    child.on("error", reject);
    child.on("close", (code) => resolve({
      code,
      stdout: Buffer.concat(out).toString("utf8"),
      stderr: Buffer.concat(err).toString("utf8"),
    }));
  });
}

test("--offline makes no network request at all", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: "session_doctor", wireTextFor: () => wireText([metadata(), ...simpleTurn(0)]) });
      const result = await runDoctor(["--offline"], { home, server });
      assert.equal(server.requests.length, 0, `offline still made ${server.requests.length} request(s)`);
      assert.match(result.stdout, /skipped \(--offline\)/);
      assert.equal(result.code, 0, result.stderr);
    });
  } finally {
    await server.close();
  }
});

test("the report names the manifest, the registry and the session state", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      await writeSession(home, { sessionId: "session_doctor", wireTextFor: () => wireText([metadata(), ...simpleTurn(0)]) });

      // A state with one blocked agent, so the report has something to say.
      // The failure must carry the fingerprint of the configuration the doctor
      // itself resolves, because that is what the guard compares against.
      const stateDir = join(home, "hook-state", "kimicode");
      await mkdir(stateDir, { recursive: true });
      const { loadConfig } = await import("../../scripts/config.mjs");
      const { configFingerprint } = await import("../../scripts/session-state.mjs");
      const cfg = loadConfig(home, { env: doctorEnv(home, server) });
      await writeFile(join(stateDir, "session_doctor.json"), JSON.stringify({
        version: 1,
        nativeSessionId: "session_doctor",
        agents: {
          main: {
            agentId: "main",
            ovSessionId: "kc-session_doctor",
            captureFailed: {
              kind: "non_retryable_delivery",
              errorKind: "http_400",
              resolved: false,
              at: Date.now(),
              configFingerprint: configFingerprint(cfg),
            },
          },
        },
      }), "utf8");

      const result = await runDoctor(["--offline"], { home, server });
      assert.match(result.stdout, /openviking-memory 0\.6\.0/, "the manifest version");
      assert.match(result.stdout, /six hooks/, "the hook registration");
      assert.match(result.stdout, /installed\.json/, "the host registry");
      assert.match(result.stdout, /unresolved capture failure/);
      // Every suggestion is a command this host or this plugin actually has.
      assert.match(result.stdout, /\/plugins install|\/plugins enable|\/plugins disable/);
      // The suggestion must be runnable from any cwd, so it is an absolute path.
      // The suggestion must be runnable from any cwd, so it is an absolute path;
      // the reporter may wrap it across lines, so match the command, not the path.
      assert.match(result.stdout, /recover\.mjs accept-loss --session session_doctor/);
      assert.doesNotMatch(result.stdout, /kimi plugin install/, "no invented CLI subcommand");
    });
  } finally {
    await server.close();
  }
});

test("a remote-environment skip is reported as a diagnosis, not a failure", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const stateDir = join(home, "hook-state", "kimicode");
      await mkdir(stateDir, { recursive: true });
      await writeFile(join(stateDir, "session_remote.json"), JSON.stringify({
        version: 1,
        nativeSessionId: "session_remote",
        agents: {},
        diagnostics: { nonLocalEnvironment: { environmentId: "remote-7", at: Date.now() } },
      }), "utf8");

      const result = await runDoctor(["--offline"], { home, server });
      assert.equal(result.code, 0, result.stderr);
      assert.match(result.stdout, /lives in environment remote-7/);
      assert.match(result.stdout, /capture is skipped by design, not failing/);
    });
  } finally {
    await server.close();
  }
});

test("the doctor never writes the pending queue it reports on", async () => {
  const server = await startFakeOv();
  try {
    await withKimiHome(async ({ home }) => {
      const pendingDir = join(home, "pending");
      await mkdir(pendingDir, { recursive: true });
      // A stale claim: `listPending` would rename it, a read-only listing does not.
      const entry = {
        type: "addMessage",
        sessionId: "kc-x",
        payload: { content: "queued" },
        createdAt: Date.now() - 60 * 60 * 1000,
        retries: 0,
        dedupKey: "k1",
      };
      const stalePath = join(pendingDir, "k1_0.processing");
      await writeFile(stalePath, JSON.stringify(entry), "utf8");
      // `recoverStaleProcessing` measures the claim's age. A file written just
      // now is not stale, so without this the assertion could never fail.
      const old = new Date(Date.now() - 60 * 60 * 1000);
      await utimes(stalePath, old, old);

      const result = await runDoctor(["--offline"], { home, server });
      assert.equal(result.code, 0, result.stderr);
      const { readdir } = await import("node:fs/promises");
      const files = await readdir(pendingDir);
      assert.deepEqual(files, ["k1_0.processing"], "a diagnostic run must not recover a claim");
    });
  } finally {
    await server.close();
  }
});
