import assert from "node:assert/strict";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const stateDir = await mkdtemp(join(tmpdir(), "ov-codex-tool-capture-"));
process.env.OPENVIKING_CODEX_STATE_DIR = stateDir;
const { catchUpTurns, hasCaptureKeyword } = await import("./ov-session.mjs");
const { extractCaptureTurns } = await import("./capture-utils.mjs");
const { loadState, saveState, withSessionLock } = await import("./session-state.mjs");
const cfg = { captureAssistantTurns: true, captureMaxLength: 24000, captureToolTraffic: false };
const marker = "PRIVATE_TOOL_MARKER";

function entry(payload) {
  return { type: "response_item", payload };
}

function message(role, text) {
  return entry({ type: "message", role, content: [{ type: "text", text }] });
}

function calls(id = "call-1") {
  return [
    entry({ type: "function_call", call_id: id, name: "shell", arguments: { command: marker } }),
    entry({ type: "function_call_output", call_id: id, output: marker }),
  ];
}

async function rollout(id, entries) {
  const path = join(stateDir, `${id}.jsonl`);
  await writeFile(path, entries.map((item) => JSON.stringify(item)).join("\n"));
  return path;
}

async function capture(id, path, sent, { config = {}, respond, shouldSend, oldCursor } = {}) {
  const locked = await withSessionLock(id, async () => {
    const state = await loadState(id);
    if (oldCursor !== undefined) {
      state.capturedTurnCount = oldCursor;
      state.captureFormatVersion = 1;
      await saveState(state);
    }
    const result = await catchUpTurns({
      state, transcriptPath: path, cfg: { ...cfg, ...config }, shouldSend,
      activePeerId: "test-peer",
      fetchJSONRes: async (url, init) => {
        const body = JSON.parse(init.body);
        const messages = body.messages || [body];
        const response = respond ? await respond(url, messages) : { ok: true, result: {} };
        if (response.ok) sent.push(...messages);
        return response;
      },
    });
    return { state, result };
  });
  return locked.value;
}

function texts(sent) {
  return sent.flatMap((item) => item.parts?.map((part) => part.text) || [item.content]);
}

function assertTextOnly(sent) {
  assert.ok(!JSON.stringify(sent).includes(marker), "tool payload must not reach upload");
  assert.ok(sent.every((item) => item.parts?.every((part) => part.type === "text")));
  assert.ok(sent.every((item) => item.peer_id === "test-peer"));
}

test("opt-out excludes normalized tool formats without excluding mixed prose", async (t) => {
  const cases = {
    function: calls(),
    custom: [
      entry({ type: "custom_tool_call", call_id: "custom", name: "patch", input: marker }),
      entry({ type: "custom_tool_call_output", call_id: "custom", output: marker }),
    ],
    discovery: [
      entry({ type: "tool_search_call", call_id: "search", arguments: { query: marker } }),
      entry({ type: "tool_search_output", call_id: "search", tools: [{ name: marker }] }),
    ],
    shell: [{ type: "event_msg", payload: {
      type: "exec_command_end", call_id: "exec", command: [marker], stdout: marker,
    } }],
    mcp: [{ type: "event_msg", payload: {
      type: "mcp_tool_call_end", call_id: "mcp", invocation: { tool: "read", arguments: { uri: marker } },
      result: { Ok: { content: [{ type: "text", text: marker }] } },
    } }],
    patch: [{ type: "event_msg", payload: {
      type: "patch_apply_end", call_id: "patch", changes: { [marker]: {} }, stdout: marker,
    } }],
    dynamic: [{ type: "event_msg", payload: {
      type: "item_completed", item: {
        type: "DynamicToolCall", id: "dynamic", tool: "plugin", arguments: { value: marker },
        content_items: [{ type: "text", text: marker }], status: "completed",
      },
    } }],
    completedMcp: [{ type: "turn_item", payload: {
      type: "McpToolCall", id: "completed-mcp", tool: "read", arguments: { value: marker },
      result: { content: [{ type: "text", text: marker }] }, status: "completed",
    } }],
    completedShell: [{ type: "turn_item", payload: {
      type: "CommandExecution", id: "completed-exec", command: [marker], aggregated_output: marker,
      status: "completed",
    } }],
    completedPatch: [{ type: "turn_item", payload: {
      type: "FileChange", id: "completed-patch", changes: { [marker]: {} }, stdout: marker,
      status: "completed",
    } }],
    subagent: [entry({ type: "sub_agent_activity", event_id: "sub", kind: marker })],
    history: [{ type: "history_mutation", payload: {
      operation: "append", items: calls().map((item) => item.payload),
    } }],
    mixed: [entry({ type: "message", role: "assistant", content: [
      { type: "text", text: "Mixed prose survives" },
      { type: "tool_use", id: "mixed", name: "read", input: { value: marker } },
      { type: "tool_result", tool_use_id: "mixed", content: marker },
    ] })],
    openaiMixed: [entry({ type: "message", role: "assistant", content: "Mixed prose survives",
      tool_calls: [{ type: "function", id: "openai", function: { name: "read", arguments: marker } }],
    })],
    objectTool: [entry({ type: "message", role: "assistant", content: {
      type: "tool_result", tool_use_id: "object", content: marker,
    } })],
    transportRole: [entry({ role: "tool", tool_call_id: "role", content: marker })],
    explicitRoleResult: [entry({ type: "tool_result", role: "user", tool_use_id: "typed", content: marker })],
    explicitRoleOutput: [entry({ type: "function_call_output", role: "user", call_id: "typed-output", output: marker, content: marker })],
  };
  for (const [name, tools] of Object.entries(cases)) {
    await t.test(name, async () => {
      const id = `format-${name}`;
      const entries = [message("user", "Please inspect the example"), ...tools, message("assistant", "Inspection complete")];
      const sent = [];
      const { state, result } = await capture(id, await rollout(id, entries), sent);
      assertTextOnly(sent);
      assert.deepEqual(texts(sent), ["Please inspect the example", ...(name.includes("ixed") ? ["Mixed prose survives"] : []), "Inspection complete"]);
      assert.equal(result.added, sent.length);
      assert.equal(result.newTurns.length, sent.length);
      assert.equal(state.capturedTurnCount, extractCaptureTurns(entries, cfg).length);
    });
  }
});

test("tools-only tail is consumed without a request or reopening a session", async () => {
  const sent = [];
  const path = await rollout("tools-only", calls());
  const first = await capture("tools-only", path, sent);
  assert.deepEqual(sent, []);
  assert.equal(first.state.ovSessionId, null);
  assert.equal(first.state.capturedTurnCount, 2);
  assert.equal((await loadState("tools-only")).capturedTurnCount, 2);
  const enabled = await capture("tools-only", path, sent, { config: { captureToolTraffic: true } });
  assert.equal(enabled.result.added, 0, "switching on does not replay excluded history");
});

test("defaults retain tool traffic and mode toggles do not skip or replay prose", async () => {
  const id = "toggle";
  const entries = [message("user", "First prompt"), ...calls("a"), message("assistant", "First answer")];
  const sent = [];
  const path = await rollout(id, entries);
  await capture(id, path, sent, { config: { captureToolTraffic: undefined } });
  assert.equal(sent.length, 4);
  assert.ok(sent.some((item) => item.parts?.some((part) => part.type === "tool")));
  entries.push(...calls("b"), message("user", "Second prompt"), message("assistant", "Second answer"));
  await rollout(id, entries);
  const second = await capture(id, path, sent);
  assert.equal(second.result.added, 2);
  assert.deepEqual(texts(sent.slice(4)), ["Second prompt", "Second answer"]);
  assertTextOnly(sent.slice(4));
  assert.equal((await capture(id, path, sent)).result.added, 0);
  entries.push(...calls("c"), message("assistant", "Third answer"));
  await rollout(id, entries);
  const third = await capture(id, path, sent, { config: { captureToolTraffic: true } });
  assert.equal(third.result.added, 3);
  assert.equal(third.state.capturedTurnCount, extractCaptureTurns(entries, cfg).length);
});

test("partial batches persist source positions and retry only unsent prose", async () => {
  const id = "partial-batch";
  const entries = Array.from({ length: 105 }, (_, i) => [
    ...calls(`batch-${i}`), message("user", `Prompt number ${i}`),
  ]).flat();
  entries.push(...calls("trailing"));
  const path = await rollout(id, entries);
  const sent = [];
  let batches = 0;
  const first = await capture(id, path, sent, { respond: async () => (++batches === 1
    ? { ok: true } : { ok: false, status: 503 }) });
  assert.equal(first.result.added, 100);
  assert.equal(first.result.newTurns.length, 105);
  assert.equal((await loadState(id)).capturedTurnCount, 300);
  const retry = await capture(id, path, sent);
  assert.equal(retry.result.added, 5);
  assert.equal(retry.state.capturedTurnCount, entries.length);
  assert.equal(new Set(texts(sent)).size, 105);
  assertTextOnly(sent);
});

test("serial fallback preserves the cursor across tools and a failed message", async () => {
  const id = "serial";
  const entries = [message("user", "First prompt"), ...calls(), message("assistant", "Second answer"), ...calls("tail")];
  const path = await rollout(id, entries);
  const sent = [];
  let serial = 0;
  const first = await capture(id, path, sent, { respond: async (url) => {
    if (url.endsWith("/batch")) return { ok: false, status: 404 };
    return ++serial === 1 ? { ok: true } : { ok: false, status: 503 };
  } });
  assert.equal(first.result.added, 1);
  assert.equal((await loadState(id)).capturedTurnCount, 1);
  const retry = await capture(id, path, sent);
  assert.equal(retry.result.added, 1);
  assert.equal(retry.state.capturedTurnCount, 6);
  assert.deepEqual(texts(sent), ["First prompt", "Second answer"]);
});

test("excluded tools cannot trigger keyword capture and veto retains pending prose", async () => {
  const id = "keyword";
  const path = await rollout(id, [
    entry({ type: "function_call_output", call_id: "keyword", output: `remember ${marker}` }),
    message("user", "Ordinary question"),
  ]);
  const sent = [];
  const veto = await capture(id, path, sent, { shouldSend: hasCaptureKeyword });
  assert.equal(veto.result.skipped, "capture_mode");
  assert.deepEqual(sent, []);
  assert.equal(veto.state.capturedTurnCount, 0);
  const accepted = await capture(id, path, sent);
  assert.equal(accepted.result.added, 1);
  assert.equal(accepted.state.capturedTurnCount, 2);
  assertTextOnly(sent);
});

test("legacy startup migration composes with tool exclusion", async () => {
  const id = "legacy-tools";
  const entries = [
    message("user", "# AGENTS.md instructions\n\n<INSTRUCTIONS>\nSynthetic instructions\n</INSTRUCTIONS>"),
    { type: "turn_context", payload: { turn_id: "first" } },
    message("user", "Already captured"), ...calls(), message("assistant", "New answer"),
  ];
  const sent = [];
  const result = await capture(id, await rollout(id, entries), sent, { oldCursor: 2 });
  assert.deepEqual(texts(sent), ["New answer"]);
  assert.equal(result.state.capturedTurnCount, extractCaptureTurns(entries, cfg).length);
  assertTextOnly(sent);
});

test("a compacted transcript resumes at the last human prompt, not a tool result", async () => {
  const id = "shrink-tools";
  const sent = [];
  const initial = Array.from({ length: 10 }, (_, i) => message("user", `Old prompt ${i}`));
  const path = await rollout(id, initial);
  await capture(id, path, sent);
  sent.length = 0;
  await rollout(id, [message("user", "Current question"), ...calls(), message("assistant", "Current answer")]);
  const resumed = await capture(id, path, sent);
  assert.deepEqual(texts(sent), ["Current question", "Current answer"]);
  assert.equal(resumed.state.capturedTurnCount, 4);
  assertTextOnly(sent);
});

test("unreadable transcript leaves persisted progress intact", async () => {
  const id = "unreadable-tools";
  const sent = [];
  const path = await rollout(id, [message("user", "Captured prompt"), ...calls()]);
  await capture(id, path, sent);
  const prior = JSON.parse(await readFile(join(stateDir, `${id}.json`), "utf-8"));
  const result = await capture(id, join(stateDir, "missing.jsonl"), sent);
  assert.equal(result.result.unreadable, true);
  assert.equal(result.state.capturedTurnCount, prior.capturedTurnCount);
  assert.equal(sent.length, 1);
});

test("text authorship, not marker matching, determines what is retained", async () => {
  const id = "quoted-text";
  const sent = [];
  const entries = [
    message("user", `The literal ${marker} is part of my question`),
    ...calls("quoted"),
    message("assistant", `My answer discusses ${marker}`),
  ];
  await capture(id, await rollout(id, entries), sent);
  assert.equal(sent.length, 2);
  assert.ok(sent.every((item) => item.parts.every((part) => part.type === "text")));
  assert.ok(texts(sent).every((text) => text.includes(marker)));
});

test("normal capture filters still redact prose alongside excluded tool data", async () => {
  const id = "filters-with-tools";
  const entries = [entry({ type: "message", role: "assistant", content: [
    { type: "text", text: "Please remember my SYNTHETIC_SECRET preference" },
    { type: "tool_use", id: "filtered", name: "shell", input: marker },
  ] })];
  const sent = [];
  await capture(id, await rollout(id, entries), sent, {
    config: { captureFilters: ["s/SYNTHETIC_SECRET/[redacted]/g"] }, shouldSend: hasCaptureKeyword,
  });
  assert.deepEqual(texts(sent), ["Please remember my [redacted] preference"]);
  assertTextOnly(sent);
});

test("type and kind user/assistant roles keep recognized prose", async () => {
  const id = "fallback-roles";
  const sent = [];
  const entries = [
    entry({ type: "user", content: [{ type: "input_text", text: "User without role field" }] }),
    entry({ kind: "assistant", content: [{ type: "output_text", text: "Assistant without role field" }] }),
  ];
  const result = await capture(id, await rollout(id, entries), sent);
  assert.deepEqual(texts(sent), ["User without role field", "Assistant without role field"]);
  assert.equal(result.state.capturedTurnCount, 2);
});

test("compact recovery does not mistake a rendered object tool for a human", async () => {
  const id = "shrink-object-tool";
  const sent = [];
  const path = await rollout(id, Array.from({ length: 10 }, (_, i) => message("user", `Old question ${i}`)));
  await capture(id, path, sent);
  sent.length = 0;
  await rollout(id, [
    message("user", "Current human question"),
    entry({ type: "message", role: "user", content: { type: "tool_result", tool_use_id: "compact-tool", content: marker } }),
    message("assistant", "Current assistant answer"),
  ]);
  await capture(id, path, sent);
  assert.deepEqual(texts(sent), ["Current human question", "Current assistant answer"]);
  assertTextOnly(sent);
});

test.after(async () => { await rm(stateDir, { recursive: true, force: true }); });
