/**
 * session_history support across the hook hosts: the post-compaction note and
 * profile through the real hook entry, the hook-side `session` fill (Cursor,
 * Qoder) and the proxy-side fill from a tool call's `_meta` (CodeBuddy, TRAE).
 */

import assert from "node:assert/strict";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  expectExit,
  runHookScript,
  scrubOpenVikingEnv,
  withMockOpenViking,
  writeJson,
} from "../../memory-plugin-shared/testing/support.mjs";
import { historySessionFor } from "../servers/mcp-proxy.mjs";

scrubOpenVikingEnv();

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const HOOK = join(ROOT, "scripts", "hook.mjs");

const POINTER = /<openviking-context source="session-history">/;

const healthOnly = (req, res) => (
  req.url === "/health" ? writeJson(res, { status: "ok" }) : writeJson(res, { status: "error" }, 404)
);

async function withHooks(fn, server = healthOnly) {
  const dir = mkdtempSync(join(tmpdir(), "ov-session-history-"));
  try {
    await withMockOpenViking(server, async (url, requests) => {
      const env = {
        HOME: dir,
        OPENVIKING_URL: url,
        OPENVIKING_CLI_CONFIG_FILE: join(dir, "missing-ovcli.conf"),
        OPENVIKING_CONFIG_FILE: join(dir, "missing-ov.conf"),
        OPENVIKING_HOOK_STATE_DIR: join(dir, "hook-state"),
        OPENVIKING_PENDING_DIR: join(dir, "pending"),
        OPENVIKING_STATE_DIR: join(dir, "state"),
        OPENVIKING_MEMORY_ENABLED: "1",
        OPENVIKING_AUTO_RECALL: "0",
        OPENVIKING_WRITE_PATH_ASYNC: "0",
      };
      const run = async (client, event, input, extra = {}) => {
        const result = expectExit(await runHookScript(HOOK, {
          argv: [event, client],
          input: { cwd: dir, ...input },
          env: { ...env, ...extra },
        }));
        const out = result.stdout.trim();
        if (!out) return null;
        try { return JSON.parse(out); } catch { return out; }
      };
      await fn(run, requests, dir);
    });
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

test("Cursor fills session_history's session from the conversation in preToolUse", async () => {
  await withHooks(async (run, requests) => {
    const call = (toolInput, toolName = "MCP:session_history") => run("cursor", "preToolUse", {
      conversation_id: "conv-1", hook_event_name: "preToolUse", tool_name: toolName, tool_input: toolInput,
    });
    assert.deepEqual(await call({ action: "search", query: "x" }), {
      updated_input: { action: "search", query: "x", session: "cu-conv-1" },
    });
    assert.deepEqual(await call({ action: "overview", session: " " }), {
      updated_input: { action: "overview", session: "cu-conv-1" },
    });
    assert.deepEqual(await call({ action: "overview", session: "cu-other" }), {});
    assert.deepEqual(await call({ action: "overview" }, "MCP:session_history_extra"), {});
    assert.deepEqual(await call('{"action":"overview"}'), {});
    assert.deepEqual(requests, []);
  });
});

test("Cursor flags the note at preCompact and adds it to the next prompt once", async () => {
  await withHooks(async (run) => {
    const base = { conversation_id: "conv-2" };
    await run("cursor", "preCompact", { ...base, trigger: "auto" });
    const first = await run("cursor", "beforeSubmitPrompt", { ...base, prompt: "go on", generation_id: "g1" });
    assert.match(first.additional_context, POINTER);
    assert.match(first.additional_context, /as cu-conv-2,/);
    assert.match(first.additional_context, /session defaults to this session/);
    const second = await run("cursor", "beforeSubmitPrompt", { ...base, prompt: "and then", generation_id: "g2" });
    assert.doesNotMatch(second.additional_context || "", POINTER);

    await run("cursor", "preCompact", { ...base, trigger: "auto" }, { OPENVIKING_SESSION_HISTORY_POINTER: "0" });
    const off = await run("cursor", "beforeSubmitPrompt", { ...base, prompt: "next", generation_id: "g3" });
    assert.doesNotMatch(off.additional_context || "", POINTER);
  });
});

test("Qoder rewrites a session_history call through mcp_call or by its direct name", async () => {
  await withHooks(async (run) => {
    const call = (toolName, toolInput) => run("qoder", "pre-tool-use", {
      session_id: "q-1", hook_event_name: "PreToolUse", tool_name: toolName, tool_input: toolInput,
    });
    assert.deepEqual(await call("mcp_call", {
      toolName: "mcp__openviking__session_history", arguments: { action: "overview" },
    }), {
      hookSpecificOutput: {
        hookEventName: "PreToolUse",
        updatedInput: {
          toolName: "mcp__openviking__session_history",
          arguments: { action: "overview", session: "qd-q-1" },
        },
      },
    });
    assert.deepEqual((await call("mcp__openviking__session_history", { action: "search", query: "x" }))
      .hookSpecificOutput.updatedInput, { action: "search", query: "x", session: "qd-q-1" });
    assert.equal(await call("mcp_call", { toolName: "mcp__openviking__read", arguments: { uri: "viking://x" } }), null);
    assert.equal(await call("mcp_call", {
      toolName: "mcp__openviking__session_history", arguments: { action: "overview", session: "qd-other" },
    }), null);
  });
});

test("Qoder answers SessionStart after a compaction or resume with the note, naming mcp_call", async () => {
  await withHooks(async (run) => {
    const resume = await run("qoder", "session-start", { session_id: "q-2", source: "resume" });
    assert.match(resume.hookSpecificOutput.additionalContext, /including anything an earlier compaction summarized/);
    // Within the SessionStart dedup window, as `-p -c '/compact'` runs it.
    const compact = await run("qoder", "session-start", { session_id: "q-2", source: "compact" });
    const text = compact.hookSpecificOutput.additionalContext;
    assert.match(text, POINTER);
    assert.match(text, /as qd-q-2,/);
    assert.match(text, /mcp_call toolName="mcp__openviking__session_history"/);
    assert.match(text, /session defaults to this session/);
    // The resumed transcript still holds that note.
    assert.equal(await run("qoder", "session-start", { session_id: "q-2", source: "resume" }), null);
    assert.match((await run("qoder", "session-start", { session_id: "q-2", source: "compact" }))
      .hookSpecificOutput.additionalContext, /including what the compaction/);
    assert.equal(await run("qoder", "session-start", { session_id: "q-3", source: "startup" }), null);
  });
});

test("CodeBuddy leaves the note from PreCompact and a resume for the next prompt", async () => {
  await withHooks(async (run) => {
    await run("codebuddy", "pre-compact", { session_id: "cb-1", transcript_path: "/nonexistent.jsonl" });
    const prompt = await run("codebuddy", "user-prompt-submit", { session_id: "cb-1", prompt: "go on" });
    assert.match(prompt.hookSpecificOutput.additionalContext, /as cb-cb-1, including what the compaction/);

    await run("codebuddy", "session-start", { session_id: "cb-2", source: "resume" });
    const resumed = await run("codebuddy", "user-prompt-submit", { session_id: "cb-2", prompt: "hi" });
    assert.match(resumed.hookSpecificOutput.additionalContext, /as cb-cb-2, including anything/);
    assert.equal(await run("codebuddy", "user-prompt-submit", { session_id: "cb-2", prompt: "again" }), null);
    await run("codebuddy", "session-start", { session_id: "cb-2", source: "resume" });
    assert.equal(await run("codebuddy", "user-prompt-submit", { session_id: "cb-2", prompt: "resumed" }), null);

    // `-c '/compact'`: SessionStart(resume), no PreCompact, SessionStart(compact) inside the dedup window.
    await run("codebuddy", "session-start", { session_id: "cb-3", source: "resume" });
    await run("codebuddy", "session-start", { session_id: "cb-3", source: "compact" });
    const compacted = await run("codebuddy", "user-prompt-submit", { session_id: "cb-3", prompt: "hi" });
    assert.match(compacted.hookSpecificOutput.additionalContext, /as cb-cb-3, including what the compaction/);
  });
});

const profileServer = (req, res) => (
  writeJson(res, { status: "ok", result: req.url.startsWith("/api/v1/content/read") ? "profile line" : [] })
);
const PROFILE = /<openviking-context source="session-start">\n[^]*profile line/;

test("CodeBuddy sends the profile again with the first prompt after a compaction", async () => {
  await withHooks(async (run) => {
    const context = async (prompt) => (await run("codebuddy", "user-prompt-submit", { session_id: "cb-p", prompt }))
      ?.hookSpecificOutput?.additionalContext || "";
    assert.doesNotMatch(await context("hi"), /source="session-start"/);
    await run("codebuddy", "pre-compact", { session_id: "cb-p", transcript_path: "/nonexistent.jsonl" });
    assert.match(await context("go on"), PROFILE);
    assert.doesNotMatch(await context("again"), /source="session-start"/);
  }, profileServer);
});

test("Qoder sends the profile on SessionStart(compact) inside the resume dedup window", async () => {
  await withHooks(async (run) => {
    await run("qoder", "session-start", { session_id: "q-p", source: "resume" });
    const compact = await run("qoder", "session-start", { session_id: "q-p", source: "compact" });
    assert.match(compact.hookSpecificOutput.additionalContext, PROFILE);
  }, profileServer);
});

test("Antigravity sends the profile again on the first turn after a compaction checkpoint", async () => {
  await withHooks(async (run, _requests, dir) => {
    const transcriptPath = join(dir, "transcript_full.jsonl");
    const steps = [["USER_EXPLICIT", "USER_INPUT", "first"], ["SYSTEM", "CHECKPOINT", "# Resuming from a compaction"],
      ["USER_EXPLICIT", "USER_INPUT", "second"], ["MODEL", "PLANNER_RESPONSE", "ok"], ["USER_EXPLICIT", "USER_INPUT", "third"]];
    writeFileSync(transcriptPath, steps.map(([source, type, content], step_index) => JSON.stringify({ step_index, source, type, content, status: "DONE" })).join("\n"));
    const turn = async (initialNumSteps, lastUserInput) => (await run("antigravity", "pre-invocation", {
      conversationId: "ag-p", invocationNum: 0, initialNumSteps, lastUserInput, transcriptPath,
    }))?.injectSteps?.[0]?.systemMessage?.systemMessage || "";
    assert.match(await turn(3, "second"), PROFILE);
    assert.doesNotMatch(await turn(5, "third"), /source="session-start"/);
  }, profileServer);
});

test("Kimi Code adds the note to the next prompt and asks the model to pass session", async () => {
  await withHooks(async (run) => {
    await run("kimicode", "pre-compact", { session_id: "k-1" });
    const text = await run("kimicode", "user-prompt-submit", { session_id: "k-1", prompt: "go on" });
    assert.match(text, POINTER);
    assert.match(text, /Pass session="kc-k-1" with each call\./);

    await run("kimicode", "session-start", { session_id: "k-9", source: "startup" });
    assert.equal(await run("kimicode", "user-prompt-submit", { session_id: "k-9", prompt: "hi" }), null);
    await run("kimicode", "session-start", { session_id: "k-9", source: "resume" });
    const resumed = await run("kimicode", "user-prompt-submit", { session_id: "k-9", prompt: "go on" });
    assert.match(resumed, /including anything an earlier compaction summarized/);
    assert.match(resumed, /Pass session="kc-k-9"/);
    assert.equal(await run("kimicode", "user-prompt-submit", { session_id: "k-9", prompt: "again" }), null);
    await run("kimicode", "session-start", { session_id: "k-9", source: "resume" });
    assert.equal(await run("kimicode", "user-prompt-submit", { session_id: "k-9", prompt: "later" }), null);
  });
});

test("TRAE answers PostCompact with the note, and stays silent with capture off", async () => {
  await withHooks(async (run) => {
    for (const [client, id] of [["trae", "tr-t-1"], ["trae-cn", "trcn-t-1"]]) {
      const out = await run(client, "post-compact", { session_id: "t-1", trigger: "auto" });
      assert.equal(out.hookSpecificOutput.hookEventName, "PostCompact");
      assert.match(out.hookSpecificOutput.additionalContext, new RegExp(`Pass session="${id}"`));
    }
    assert.deepEqual(await run("trae", "post-compact", { session_id: "t-1" }, { OPENVIKING_AUTO_CAPTURE: "0" }),
      { decision: "approve" });
  });
});

test("the proxy fills session from CodeBuddy's baggage and TRAE's chatSessionId", () => {
  const codebuddy = historySessionFor("codebuddy");
  const parent = "01a1edd8-f647-7a87-a440-978ca0a0122a";
  assert.equal(codebuddy({ _meta: {
    "workbuddy.ai/conversationId": parent,
    baggage: `codebuddy.session_id=${parent},codebuddy.conversation_request_id=r1`,
  } }), `cb-${parent}`);
  // A subagent's call: its own conversation, the parent's session in the baggage.
  assert.equal(codebuddy({ _meta: {
    "workbuddy.ai/conversationId": "01a1edd9-0c00-7f18-90ba-1ae0da54baf0",
    baggage: `codebuddy.conversation_request_id=r2,codebuddy.session_id=${parent}`,
  } }), `cb-${parent}`);
  assert.equal(codebuddy({ _meta: { "workbuddy.ai/conversationId": "c-1" } }), "cb-c-1");
  assert.equal(codebuddy({ _meta: { progressToken: 1 } }), "");
  assert.equal(codebuddy({}), "");

  assert.equal(historySessionFor("trae")({ _meta: { chatSessionId: "6a5f11247f3b188693db4ca7" } }),
    "tr-6a5f11247f3b188693db4ca7");
  assert.equal(historySessionFor("trae-cn")({ _meta: { chatSessionId: "019fb79d-aaaa" } }), "trcn-019fb79d-aaaa");
  assert.equal(historySessionFor("trae")({ _meta: {} }), "");
  for (const client of ["cursor", "qoder", "kimicode", "zcode", "unknown", ""]) {
    assert.equal(historySessionFor(client), null, client);
  }
});
