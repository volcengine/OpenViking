import assert from "node:assert/strict";
import { rm } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { createSessionInject } from "../opencode-plugin/lib/session-inject.mjs";
import {
  buildConfigForTest, expectExit, runHookScript, scrubOpenVikingEnv,
  withMockOpenViking, writeCredentialFiles, writeJson,
} from "./testing/support.mjs";

function respond(req, res) {
  const path = new URL(req.url, "http://localhost").pathname;
  let result = [];
  if (path.endsWith("/context")) result = { latest_archive_overview: "Old archive summary." };
  if (path.endsWith("/status")) result = { user: "default", user_id: "default" };
  if (path.endsWith("/read")) result = "Keep the user profile available.";
  writeJson(res, { status: "ok", result });
}

for (const source of ["resume", "compact"]) {
  for (const enabled of [false, true]) {
    test(`CC ${source}: archive opt-in=${enabled}, profile remains available`, async () => {
      const restore = scrubOpenVikingEnv();
      let files;
      try {
        await withMockOpenViking(respond, async (baseUrl, requests) => {
          files = await writeCredentialFiles("ov-start-", {
            ovcli: { url: baseUrl, plugin: enabled ? { resumeArchiveInject: true } : {} },
          });
          const result = expectExit(await runHookScript(
            fileURLToPath(new URL("../claude-code-memory-plugin/scripts/session-start.mjs", import.meta.url)),
            {
              input: { session_id: "host", source, cwd: files.dir },
              cwd: files.dir,
              env: { ...files.env, OPENVIKING_STATE_DIR: join(files.dir, "state"), OPENVIKING_PENDING_DIR: join(files.dir, "pending") },
            },
          ));
          const context = JSON.parse(result.stdout).hookSpecificOutput?.additionalContext ?? "";
          assert.match(context, /Keep the user profile available/);
          assert.equal(context.includes("Old archive summary."), enabled);
          assert.equal(requests.some((request) => request.path.endsWith("/context")), enabled);
        });
      } finally {
        restore();
        if (files) await rm(files.dir, { recursive: true, force: true });
      }
    });
  }
}

for (const enabled of [false, true]) {
  test(`OpenCode archive opt-in=${enabled}, profile remains available`, async () => {
    await withMockOpenViking(respond, async (baseUrl, requests) => {
      const config = { ...buildConfigForTest("opencode"), endpoint: baseUrl, resumeArchiveInject: enabled };
      const injection = createSessionInject({ config, sessionManager: { getMappedSessionId: () => "host" } });
      const block = await injection.buildSessionContext({ sessionID: "host", messageID: "m1" });
      assert.match(block, /Keep the user profile available/);
      assert.equal(block.includes("Old archive summary."), enabled);
      assert.equal(requests.some((request) => request.path.endsWith("/context")), enabled);
    });
  });
}
