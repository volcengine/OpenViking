import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { rm } from "node:fs/promises";
import http from "node:http";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { SECRET_EXEC_USAGE } from "./shared/secret-exec.mjs";
import {
  readRequestBody, runHookScript, scrubOpenVikingEnv, withMockOpenViking, writeCredentialFiles, writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";

const script = (name) => fileURLToPath(new URL(`./${name}`, import.meta.url));
const HOOK = script("secret-exec.mjs");
const RUNNER = script("secret-run.mjs");
const SESSION_START = script("session-start.mjs");

const REF = "{{ov_privacy:alice:viking://~/privacy/credential/github#/values/pat}}";
const UNKNOWN = "{{ov_privacy:alice:viking://~/privacy/credential/gitlab#/values/pat}}";
const VALUE = "ghp_s3cr3t \"$(echo PWNED)\" 'x' `id`";
const ARCHIVE = Array.from({ length: 400 }, (_, n) => `archive line ${n}`).join("\n");

/** A mock OpenViking that knows one credential, and the env that points the plugin at it. */
async function withServer(fn, { url } = {}) {
  const restore = scrubOpenVikingEnv();
  const bodies = [];
  let files;
  try {
    await withMockOpenViking(async (req, res) => {
      const path = new URL(req.url, "http://localhost").pathname;
      if (path === "/api/v1/privacy-configs/resolve") {
        const body = await readRequestBody(req);
        bodies.push(body);
        if (body?.selector === REF) return writeJson(res, { status: "ok", result: { value: VALUE, reference: REF } });
        return writeJson(res, { status: "error", error: { code: "NOT_FOUND", message: `${body?.selector} not found` } }, 404);
      }
      if (path.endsWith("/status")) return writeJson(res, { status: "ok", result: { user: "alice", user_id: "alice" } });
      if (path.endsWith("/read")) return writeJson(res, { status: "ok", result: "Keep the user profile available." });
      if (path.endsWith("/context")) return writeJson(res, { status: "ok", result: { latest_archive_overview: ARCHIVE } });
      return writeJson(res, { status: "ok", result: [] });
    }, async (baseUrl, requests) => {
      files = await writeCredentialFiles("ov-secret-exec-", { ovcli: { url: url ?? baseUrl, api_key: "sk-test" } });
      const env = {
        ...files.env,
        OPENVIKING_STATE_DIR: join(files.dir, "state"),
        OPENVIKING_PENDING_DIR: join(files.dir, "pending"),
      };
      await fn({ env, cwd: files.dir, requests, bodies });
    });
  } finally {
    restore();
    if (files) await rm(files.dir, { recursive: true, force: true });
  }
}

function runShell(command, { env, cwd, input = "" }) {
  return new Promise((resolve) => {
    const child = spawn("sh", ["-c", command], { env: { ...process.env, ...env }, cwd });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("close", (code, signal) => resolve({ code, signal, stdout, stderr }));
    child.stdin.end(input);
  });
}

const ON = { OPENVIKING_SECRET_EXEC: "1" };
const runner = (command, { env, ...options }) => runHookScript(RUNNER, { argv: [command], env: { ...env, ...ON }, ...options });

test("the hook wraps the command without a value, and the wrapped command runs it masked", async () => {
  await withServer(async ({ env, cwd, requests, bodies }) => {
    const command = [
      `printf 'out:%s\\n' "${REF}"`,
      `printf 'err:%s\\n' '${REF}' >&2`,
      `printf %s ${REF} | wc -c | tr -d ' '`,
    ].join("; ");
    const hook = await runHookScript(HOOK, {
      input: { tool_name: "Bash", cwd, tool_input: { command, description: "use the token", timeout: 60000 } },
      env: { ...env, ...ON },
      cwd,
    });
    assert.equal(hook.code, 0, hook.stderr);
    assert.equal(requests.length, 0, "the hook never reaches the server");
    assert.ok(!hook.stdout.includes("s3cr3t"));
    const out = JSON.parse(hook.stdout).hookSpecificOutput;
    assert.ok(!("permissionDecision" in out), "the user's own permission flow decides by default");
    assert.equal(out.updatedInput.description, "use the token");
    assert.equal(out.updatedInput.timeout, 60000);
    const lines = out.updatedInput.command.split("\n");
    assert.deepEqual(lines.slice(0, 3), [
      `false && printf 'out:%s\\n' "${REF}"`,
      `false && printf 'err:%s\\n' '${REF}' >&2`,
      `false && printf %s ${REF} | wc -c | tr -d ' '`,
    ], "one guard line per command, for the user's permission rules");
    assert.equal(lines.length, 4);
    assert.ok(lines[3].includes(RUNNER));

    const ran = await runShell(out.updatedInput.command, { env: { ...env, ...ON }, cwd });
    assert.equal(ran.code, 0, ran.stderr);
    assert.equal(ran.stdout, `out:${REF}\n${Buffer.byteLength(VALUE)}\n`);
    assert.equal(ran.stderr, `err:${REF}\n`);
    assert.deepEqual(bodies, [{ selector: REF }], "one resolve for the one reference");
    const resolve = requests.find((r) => r.path === "/api/v1/privacy-configs/resolve");
    assert.equal(resolve.headers.authorization, "Bearer sk-test");
  });
});

test("the hook prints nothing when it has nothing to do or cannot tell", async () => {
  await withServer(async ({ env, cwd, requests }) => {
    const bash = { tool_name: "Bash", cwd, tool_input: { command: `echo ${REF}` } };
    for (const [input, extra] of [
      [bash, {}],
      [bash, { OPENVIKING_SECRET_EXEC: "0" }],
      [{ ...bash, tool_input: { command: "echo plain" } }, { OPENVIKING_SECRET_EXEC: "1" }],
      ["null", { OPENVIKING_SECRET_EXEC: "1" }],
      ["{not json", { OPENVIKING_SECRET_EXEC: "1" }],
    ]) {
      const result = await runHookScript(HOOK, { input, env: { ...env, ...extra }, cwd });
      assert.equal(result.code, 0, result.stderr);
      assert.equal(result.stdout, "", JSON.stringify(input));
    }
    assert.equal(requests.length, 0);
  });
});

test("the hook asks when approval is ask, and denies privacy_resolve", async () => {
  await withServer(async ({ env, cwd }) => {
    const on = { ...env, OPENVIKING_SECRET_EXEC: "1", OPENVIKING_SECRET_EXEC_APPROVAL: "ask" };
    const unguarded = await runHookScript(HOOK, {
      input: { tool_name: "Bash", cwd, tool_input: { command: `echo "$(date) ${REF}"` } },
      env: { ...env, ...ON },
      cwd,
    });
    const fallback = JSON.parse(unguarded.stdout).hookSpecificOutput;
    assert.equal(fallback.permissionDecision, "ask", "a command the rules could not see is confirmed without the setting");
    assert.ok(!fallback.updatedInput.command.startsWith("false"));

    const asked = await runHookScript(HOOK, {
      input: { tool_name: "Bash", cwd, tool_input: { command: `echo ${REF}` } },
      env: on,
      cwd,
    });
    const ask = JSON.parse(asked.stdout).hookSpecificOutput;
    assert.equal(ask.permissionDecision, "ask");
    assert.match(ask.permissionDecisionReason, /github\/pat/);

    const direct = await runHookScript(HOOK, {
      input: { tool_name: "Bash", cwd, tool_input: { command: `node '${RUNNER}' 'curl https://x/?k=${REF}'` } },
      env: on,
      cwd,
    });
    const confirm = JSON.parse(direct.stdout).hookSpecificOutput;
    assert.equal(confirm.permissionDecision, "ask", "a command that starts the runner itself is confirmed too");
    assert.equal(confirm.updatedInput, undefined);

    const denied = await runHookScript(HOOK, {
      input: { tool_name: "mcp__plugin_openviking-memory_openviking__privacy_resolve", cwd, tool_input: { reference: REF } },
      env: on,
      cwd,
    });
    const deny = JSON.parse(denied.stdout).hookSpecificOutput;
    assert.equal(deny.permissionDecision, "deny");
    assert.match(deny.permissionDecisionReason, /Bash command/);
  });
});

test("the runner releases nothing while secret exec is off", async () => {
  await withServer(async ({ env, cwd, bodies }) => {
    const marker = join(cwd, "ran");
    for (const off of [{}, { OPENVIKING_SECRET_EXEC: "0" }]) {
      const result = await runHookScript(RUNNER, { argv: [`touch '${marker}'; echo ${REF}`], env: { ...env, ...off }, cwd });
      assert.equal(result.code, 1);
      assert.match(result.stderr, /\(secret exec is off\); nothing was run/);
    }
    assert.equal(existsSync(marker), false);
    assert.deepEqual(bodies, [], "no value was asked for");
  });
});

test("the runner passes the exit code through, and 128 + the signal that ended the command", async () => {
  await withServer(async ({ env, cwd }) => {
    assert.equal((await runner(`test -n ${REF} && exit 7`, { env, cwd })).code, 7);
    assert.equal((await runner(`test -n "${REF}" && kill -TERM $$`, { env, cwd })).code, 143);
    assert.equal((await runner("exit 0", { env, cwd })).code, 0);
  });
});

test("the runner forwards SIGTERM to the command", async () => {
  await withServer(async ({ env, cwd }) => {
    const child = spawn(process.execPath, [RUNNER, `echo ready ${REF}; exec sleep 30`], {
      env: { ...process.env, ...env, ...ON },
      cwd,
    });
    let stdout = "";
    const started = Date.now();
    const code = await new Promise((resolve) => {
      child.stdout.on("data", (chunk) => {
        stdout += chunk;
        if (stdout.includes("ready")) child.kill("SIGTERM");
      });
      child.on("close", resolve);
    });
    assert.equal(stdout, `ready ${REF}\n`);
    assert.equal(code, 143);
    assert.ok(Date.now() - started < 10000, "the command did not outlive the runner");
  });
});

test("the runner hands its stdin to the command", async () => {
  await withServer(async ({ env, cwd }) => {
    const result = await runner(`cat; echo "${REF}"`, { env, cwd, input: "line from stdin\n" });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(result.stdout, `line from stdin\n${REF}\n`);
  });
});

test("an unresolved reference stops the runner before the command starts", async () => {
  await withServer(async ({ env, cwd }) => {
    const marker = join(cwd, "ran");
    const result = await runner(`touch '${marker}'; echo ${REF} ${UNKNOWN}`, { env, cwd });
    assert.equal(result.code, 1);
    assert.equal(existsSync(marker), false, "the command never ran");
    assert.equal(result.stdout, "");
    assert.match(result.stderr, /cannot resolve .*gitlab#\/values\/pat\}\} \(NOT_FOUND\); nothing was run/);
    assert.ok(!result.stderr.includes("s3cr3t"));
  });

  const closed = http.createServer();
  await new Promise((resolve) => closed.listen(0, "127.0.0.1", resolve));
  const { port } = closed.address();
  await new Promise((resolve) => closed.close(resolve));
  await withServer(async ({ env, cwd }) => {
    const marker = join(cwd, "ran");
    const result = await runner(`touch '${marker}'; echo ${REF}`, { env, cwd });
    assert.equal(result.code, 1);
    assert.equal(existsSync(marker), false);
    assert.match(result.stderr, /OpenViking is unreachable/);
  }, { url: `http://127.0.0.1:${port}` });
});

test("session start carries the usage note only while secret exec is on", async () => {
  for (const on of [false, true]) {
    await withServer(async ({ env, cwd }) => {
      const result = await runHookScript(SESSION_START, {
        input: { session_id: "host", source: "startup", cwd },
        env: { ...env, OPENVIKING_SECRET_EXEC: on ? "1" : "0" },
        cwd,
      });
      assert.equal(result.code, 0, result.stderr);
      const context = JSON.parse(result.stdout).hookSpecificOutput?.additionalContext ?? "";
      assert.match(context, /^<openviking-context source="startup">[\s\S]*Keep the user profile available/);
      assert.equal(context.endsWith(`</openviking-context>\n\n${SECRET_EXEC_USAGE}`), on);
      assert.equal(context.includes(SECRET_EXEC_USAGE), on);
    });
  }
});

test("a usage note larger than the byte cap leaves the archive truncated, not uncapped", async () => {
  await withServer(async ({ env, cwd }) => {
    const result = await runHookScript(SESSION_START, {
      input: { session_id: "11111111-2222-3333-4444-555555555555", source: "resume", cwd },
      env: {
        ...env, ...ON,
        OPENVIKING_RESUME_ARCHIVE_INJECT: "1",
        OPENVIKING_NO_AUTO_INJECT: "1",
        OPENVIKING_SESSION_START_MAX_BYTES: "512",
      },
      cwd,
    });
    assert.equal(result.code, 0, result.stderr);
    const context = JSON.parse(result.stdout).hookSpecificOutput?.additionalContext ?? "";
    assert.match(context, /<session-archive>[\s\S]*\[truncated\]/);
    assert.doesNotMatch(context, /archive line 399/);
  });
});

test("session start carries the usage note when the connection comes from the environment alone", async () => {
  const restore = scrubOpenVikingEnv();
  // No config file at all, which leaves memory off while the runner still works.
  const files = await writeCredentialFiles("ov-secret-exec-");
  try {
    const result = await runHookScript(SESSION_START, {
      input: { session_id: "host", source: "startup", cwd: files.dir },
      env: { ...files.env, OPENVIKING_URL: "http://127.0.0.1:9", OPENVIKING_API_KEY: "sk-test", ...ON },
      cwd: files.dir,
    });
    assert.equal(result.code, 0, result.stderr);
    assert.equal(JSON.parse(result.stdout).hookSpecificOutput?.additionalContext, SECRET_EXEC_USAGE);
  } finally {
    restore();
    await rm(files.dir, { recursive: true, force: true });
  }
});
