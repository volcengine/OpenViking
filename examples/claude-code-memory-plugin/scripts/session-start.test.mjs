import assert from "node:assert/strict";
import { readFile, rm } from "node:fs/promises";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  expectExit,
  runHookScript,
  scrubOpenVikingEnv,
  withMockOpenViking,
  writeCredentialFiles,
  writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";

const SCRIPTS = dirname(fileURLToPath(import.meta.url));
const FAKE_CLAUDE_PID = "424242";

function respond(req, res) {
  const path = new URL(req.url, "http://localhost").pathname;
  let result = [];
  if (path.endsWith("/status")) result = { user: "default", user_id: "default" };
  if (path.endsWith("/read")) result = "Keep the user profile available.";
  writeJson(res, { status: "ok", result });
}

async function runSessionStart({ source, baseUrl = "http://127.0.0.1:1", env = {}, sessionId = "host" }) {
  const files = await writeCredentialFiles("ov-cc-start-", { ovcli: { url: baseUrl } });
  try {
    const result = expectExit(await runHookScript(join(SCRIPTS, "session-start.mjs"), {
      input: { session_id: sessionId, source, cwd: files.dir },
      cwd: files.dir,
      env: {
        ...files.env,
        OPENVIKING_PENDING_DIR: join(files.dir, "pending"),
        OPENVIKING_TIMEOUT_MS: "2000",
        CLAUDE_PID: FAKE_CLAUDE_PID,
        ...env,
      },
    }));
    const context = JSON.parse(result.stdout).hookSpecificOutput?.additionalContext ?? "";
    let binding = null;
    try {
      binding = JSON.parse(await readFile(join(files.env.OPENVIKING_HOME, "state", `cc-session-${FAKE_CLAUDE_PID}.json`), "utf-8"));
    } catch { /* not written */ }
    return { context, binding };
  } finally {
    await rm(files.dir, { recursive: true, force: true });
  }
}

const POINTER = /<openviking-context source="session-history">[\s\S]*cc-host[\s\S]*session_history[\s\S]*<\/openviking-context>/;

for (const source of ["compact", "resume"]) {
  test(`SessionStart ${source} adds the session-history pointer after the profile`, async () => {
    const restore = scrubOpenVikingEnv();
    try {
      await withMockOpenViking(respond, async (baseUrl) => {
        const { context } = await runSessionStart({ source, baseUrl });
        assert.match(context, /Keep the user profile available/);
        assert.match(context, POINTER);
        // A sibling block, so the capture sanitizer strips both.
        assert.ok(context.indexOf('source="session-history"') > context.indexOf(`source="${source}"`));
        assert.equal(context.match(/source="session-history"/g).length, 1);
      });
    } finally {
      restore();
    }
  });
}

test("SessionStart startup and clear add no pointer", async () => {
  const restore = scrubOpenVikingEnv();
  try {
    await withMockOpenViking(respond, async (baseUrl) => {
      for (const source of ["startup", "clear"]) {
        const { context } = await runSessionStart({ source, baseUrl });
        assert.doesNotMatch(context, /session-history/, source);
      }
    });
  } finally {
    restore();
  }
});

test("the pointer is switched off by config, and goes with capture", async () => {
  const restore = scrubOpenVikingEnv();
  try {
    await withMockOpenViking(respond, async (baseUrl) => {
      const off = await runSessionStart({ source: "compact", baseUrl, env: { OPENVIKING_SESSION_HISTORY_POINTER: "0" } });
      assert.match(off.context, /Keep the user profile available/);
      assert.doesNotMatch(off.context, /session-history/);
      const noCapture = await runSessionStart({ source: "compact", baseUrl, env: { OPENVIKING_AUTO_CAPTURE: "0" } });
      assert.doesNotMatch(noCapture.context, /session-history/);
    });
  } finally {
    restore();
  }
});

test("the pointer needs no server: it is still added when OpenViking is unreachable", async () => {
  const restore = scrubOpenVikingEnv();
  try {
    const { context } = await runSessionStart({ source: "compact" });
    assert.match(context, POINTER);
    assert.doesNotMatch(context, /Keep the user profile available/);
  } finally {
    restore();
  }
});

test("every SessionStart records the session as current for the MCP proxy", async () => {
  const restore = scrubOpenVikingEnv();
  try {
    for (const source of ["startup", "clear", "resume", "compact"]) {
      const { binding } = await runSessionStart({ source, sessionId: `s-${source}` });
      assert.equal(binding?.session_id, `s-${source}`, source);
    }
    // A bypassed session runs no injection but is still the current one.
    const bypassed = await runSessionStart({
      source: "clear",
      sessionId: "s-bypassed",
      env: { OPENVIKING_BYPASS_SESSION: "1" },
    });
    assert.equal(bypassed.binding?.session_id, "s-bypassed");
    assert.equal(bypassed.context, "");
  } finally {
    restore();
  }
});
