/**
 * Auto-recall entry point (DESIGN.md §4): the hook answers within the host's
 * UserPromptSubmit budget even when the server accepts the connection and then
 * never answers, and the off switch really removes the network side effect.
 */

import assert from "node:assert/strict";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  runHookScript,
  scrubOpenVikingEnv,
  withMockOpenViking,
  writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const PROMPT = JSON.stringify({
  session_id: "recall-budget",
  prompt: "what did we decide about the installer validation path?",
});

function stateDir() {
  const dir = mkdtempSync(join(tmpdir(), "kimicode-recall-"));
  return { dir, cleanup: () => rmSync(dir, { recursive: true, force: true }) };
}

test("a stalled search is abandoned inside the hook budget", async () => {
  const restore = scrubOpenVikingEnv();
  const state = stateDir();
  try {
    const started = Date.now();
    const { result, requests } = await withMockOpenViking(
      async (req, res) => {
        if (new URL(req.url, "http://127.0.0.1").pathname === "/health") {
          writeJson(res, { status: "ok", result: { ok: true } });
          return;
        }
        // Accept the request and never answer: the case a per-request timeout
        // alone cannot bound, because each retry gets a fresh one.
        await new Promise(() => {});
      },
      async (base, seen) => ({
        requests: seen,
        result: await runHookScript(join(SCRIPT_DIR, "auto-recall.mjs"), {
          input: PROMPT,
          env: {
            OPENVIKING_URL: base,
            OPENVIKING_RECALL_TIMEOUT_MS: "800",
            OPENVIKING_KIMICODE_STATE_DIR: join(state.dir, "state"),
          },
        }),
      }),
    );

    assert.equal(result.code, 0);
    assert.equal(String(result.stdout || "").trim(), "");
    // The hook must have reached the search: an empty answer with no request
    // would pass this test even if recall never ran.
    assert.ok(requests.some((entry) => entry.path === "/health"), "the hook must check health first");
    assert.ok(
      requests.some((entry) => entry.path.startsWith("/api/v1/")),
      `expected a search request, saw ${requests.map((entry) => entry.path).join(", ")}`,
    );
    assert.ok(Date.now() - started < 4000, "the hook must not outlive its 800ms budget by seconds");
  } finally {
    state.cleanup();
    restore();
  }
});

test("a finished recall is emitted when the profile request stalls", async () => {
  const restore = scrubOpenVikingEnv();
  const state = stateDir();
  const recalled = "we decided the installer must validate the manifest at the repository root";
  try {
    const started = Date.now();
    const { result, requests } = await withMockOpenViking(
      async (req, res) => {
        const path = new URL(req.url, "http://127.0.0.1").pathname;
        if (path === "/health") {
          writeJson(res, { status: "ok", result: { ok: true } });
          return;
        }
        if (path.startsWith("/api/v1/search/")) {
          // The context face answers the session-aware search route with an
          // assembled block; without `rendered` the hook would read it as
          // "nothing relevant" and never arm the deadline payload.
          writeJson(res, {
            status: "ok",
            result: {
              rendered: recalled,
              entries: [
                { uri: "viking://user/default/memories/installer-decision", category: "memory", text: recalled, score: 0.9 },
              ],
              digest: "",
              stats: {},
            },
          });
          return;
        }
        // The agent-profile request (and anything that follows) never answers.
        await new Promise(() => {});
      },
      async (base, seen) => ({
        requests: seen,
        result: await runHookScript(join(SCRIPT_DIR, "auto-recall.mjs"), {
          input: PROMPT,
          env: {
            OPENVIKING_URL: base,
            OPENVIKING_RECALL_TIMEOUT_MS: "1200",
            OPENVIKING_KIMICODE_STATE_DIR: join(state.dir, "state"),
          },
        }),
      }),
    );

    assert.equal(result.code, 0);
    assert.ok(
      String(result.stdout || "").includes(recalled),
      "the deadline must emit the recall that already finished"
        + ` (stdout was ${JSON.stringify(String(result.stdout || ""))},`
        + ` requests were ${requests.map((entry) => entry.path).join(", ")})`,
    );
    assert.ok(
      requests.some((entry) => entry.path.startsWith("/api/v1/search/")),
      `expected a search request, saw ${requests.map((entry) => entry.path).join(", ")}`,
    );
    assert.ok(
      requests.some((entry) => entry.path === "/api/v1/system/status"),
      "the profile request must have started and stalled",
    );
    assert.ok(Date.now() - started < 5000, "the hook must not outlive its 1200ms budget by seconds");
  } finally {
    state.cleanup();
    restore();
  }
});

test("the off switch removes the network side effect entirely", async () => {
  const restore = scrubOpenVikingEnv();
  const state = stateDir();
  try {
    const { result, requests } = await withMockOpenViking(
      async (req, res) => {
        writeJson(res, { status: "ok", result: { ok: true } });
      },
      async (base, seen) => ({
        requests: seen,
        result: await runHookScript(join(SCRIPT_DIR, "auto-recall.mjs"), {
          input: PROMPT,
          env: {
            OPENVIKING_URL: base,
            OPENVIKING_AUTO_RECALL: "false",
            OPENVIKING_KIMICODE_STATE_DIR: join(state.dir, "state"),
          },
        }),
      }),
    );

    assert.equal(result.code, 0);
    assert.equal(String(result.stdout || "").trim(), "");
    assert.equal(requests.length, 0, "a disabled hook must not call the server");
  } finally {
    state.cleanup();
    restore();
  }
});
