/**
 * Auto-recall entry point (DESIGN.md §4): the hook answers within the host's
 * UserPromptSubmit budget even when the server accepts the connection and then
 * never answers, and it answers with nothing rather than blocking the prompt.
 */

import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { runHookScript, withMockOpenViking, writeJson } from "../../memory-plugin-shared/testing/support.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));

test("a stalled search is abandoned inside the hook budget", async () => {
  const stateDir = mkdtempSync(join(tmpdir(), "kimicode-recall-"));
  try {
    const started = Date.now();
    const result = await withMockOpenViking(
      async (req, res) => {
        if (new URL(req.url, "http://127.0.0.1").pathname === "/health") {
          writeJson(res, { status: "ok", result: { ok: true } });
          return;
        }
        // Accept the request and never answer: the case a per-request timeout
        // alone cannot bound, because each retry gets a fresh one.
        await new Promise(() => {});
      },
      async (base) => runHookScript(join(SCRIPT_DIR, "auto-recall.mjs"), {
        input: JSON.stringify({
          session_id: "recall-budget",
          prompt: "what did we decide about the installer validation path?",
        }),
        env: {
          ...process.env,
          OPENVIKING_URL: base,
          OPENVIKING_RECALL_TIMEOUT_MS: "800",
          OPENVIKING_KIMICODE_STATE_DIR: join(stateDir, "state"),
        },
      }),
    );

    assert.equal(result.code, 0);
    assert.equal(String(result.stdout || "").trim(), "");
    assert.ok(Date.now() - started < 8000, "the hook must not outlive its budget");
  } finally {
    rmSync(stateDir, { recursive: true, force: true });
  }
});
