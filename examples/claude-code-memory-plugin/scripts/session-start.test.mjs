import assert from "node:assert/strict";
import { rm } from "node:fs/promises";
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

function respond(req, res) {
  const path = new URL(req.url, "http://localhost").pathname;
  let result = [];
  if (path.endsWith("/status")) result = { user: "default", user_id: "default" };
  if (path.endsWith("/read")) result = "Keep the user profile available.";
  writeJson(res, { status: "ok", result });
}

async function runSessionStart(source, baseUrl, env = {}) {
  const files = await writeCredentialFiles("ov-cc-start-", { ovcli: { url: baseUrl } });
  try {
    const result = expectExit(await runHookScript(join(SCRIPTS, "session-start.mjs"), {
      input: { session_id: "host", source, cwd: files.dir },
      cwd: files.dir,
      env: { ...files.env, OPENVIKING_PENDING_DIR: join(files.dir, "pending"), OPENVIKING_TIMEOUT_MS: "2000", ...env },
    }));
    return JSON.parse(result.stdout).hookSpecificOutput?.additionalContext ?? "";
  } finally {
    await rm(files.dir, { recursive: true, force: true });
  }
}

const POINTER = /<openviking-context source="session-history">[\s\S]*cc-host[\s\S]*session_history[\s\S]*<\/openviking-context>/;

test("SessionStart adds the session-history pointer after compact and resume, unless switched off", async () => {
  const restore = scrubOpenVikingEnv();
  try {
    await withMockOpenViking(respond, async (baseUrl) => {
      for (const source of ["compact", "resume"]) {
        const context = await runSessionStart(source, baseUrl);
        assert.match(context, /Keep the user profile available/, source);
        assert.match(context, POINTER, source);
      }
      const off = await runSessionStart("compact", baseUrl, { OPENVIKING_SESSION_HISTORY_POINTER: "0" });
      assert.match(off, /Keep the user profile available/);
      assert.doesNotMatch(off, /session-history/);
    });
  } finally {
    restore();
  }
});
