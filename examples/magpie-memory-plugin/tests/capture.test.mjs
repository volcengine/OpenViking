import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test, { mock } from "node:test";
import childProcess from "node:child_process";
import { syncBuiltinESMExports } from "node:module";
import { EventEmitter } from "node:events";
import { loadConfig } from "../config.mjs";
import { queueCapture, nativeOwner, captureDirectory, finishCapture, prepareCapture } from "../capture.mjs";
import { captureMessages, completedReply } from "../capture-messages.mjs";
import { runCaptureWorker } from "../capture-worker.mjs";
import { connectionScope } from "../scope.mjs";
import { onRequest, onResponse } from "../hooks.mjs";
import { recallRequest } from "../runtime.mjs";

// These tests drain workers explicitly with fake HTTP. Do not leave detached
// processes racing fixture cleanup or making real network requests.
mock.method(childProcess, "spawn", () => Object.assign(new EventEmitter(), { unref() {} }));
syncBuiltinESMExports();

async function fixture(t) {
  const dir = await mkdtemp(join(tmpdir(), "ov-magpie-capture-"));
  t.after(() => rm(dir, { recursive: true, force: true }));
  const configPath = join(dir, "ovcli.conf");
  const dataDir = join(dir, "state");
  const file = { url: "http://127.0.0.1:1", api_key: "fixture", account: "default", user: "default",
    actor_peer_id: "test-project", plugin: { magpie: { autoCapture: true, dataDir } } };
  await writeFile(configPath, JSON.stringify(file));
  const cfg = await loadConfig({ OPENVIKING_CLI_CONFIG_FILE: configPath });
  const ctx = { origin: "local", protocol: "chat", session: "conversation", agent: "pi", cwd: "/fixture", state: {} };
  const messages = [ { role: "user", content: "Remember my project preference" }, { role: "assistant", content: "I understand your project preference" } ];
  const sessions = new Map();
  const writes = [], creates = [], commits = [];
  let loseAck = false, offline = false;
  const call = async (_, path, init = {}) => {
    if (offline) throw Object.assign(new Error("offline"), { code: "unavailable" });
    if (path === "/api/v1/sessions") {
      creates.push(init.body);
      if (sessions.has(init.body.session_id)) throw Object.assign(new Error("exists"), { status: 409 });
      sessions.set(init.body.session_id, { uri: `viking://user/default/sessions/${init.body.session_id}`, rows: [], archive: [], total: 0 });
      return {};
    }
    const id = /\/sessions\/([^/?]+)/.exec(path)?.[1];
    if (path.endsWith("/commit")) {
      const session = sessions.get(id);
      session.archive.push(...session.rows); session.rows = []; session.commits = (session.commits || 0) + 1;
      commits.push(id);
      return { archive_uri: `${session.uri}/history/archive_001` };
    }
    if (id && !/\/messages(?:\/batch)?$/.test(path)) {
      if (!sessions.has(id)) throw Object.assign(new Error("missing"), { status: 404 });
      const s = sessions.get(id);
      return { uri: s.uri, total_message_count: s.total, message_count: s.rows.length, commit_count: s.commits || 0 };
    }
    if (/\/messages(?:\/batch)?$/.test(path)) {
      const s = sessions.get(id);
      const messages = path.endsWith("/batch") ? init.body.messages : [init.body];
      s.rows.push(...messages); s.total += messages.length;
      writes.push(messages);
      if (loseAck) { loseAck = false; throw Object.assign(new Error("lost acknowledgement"), { code: "timeout" }); }
      return {};
    }
    const url = new URL(path, "http://fixture");
    const uri = url.searchParams.get("uri");
    const s = [...sessions.values()].find((s) => uri.startsWith(s.uri));
    if (path.startsWith("/api/v1/fs/ls")) return s.archive.length ? [{ name: "archive_001", isDir: true }] : [];
    if (path.startsWith("/api/v1/content/read")) {
      const rows = uri.includes("/history/") ? s.archive : s.rows;
      return rows.map((row) => JSON.stringify(row)).join("\n");
    }
    throw new Error(`unexpected path ${path}`);
  };
  const queue = (rows = messages, currentCtx = ctx) => queueCapture(cfg, currentCtx, rows, { launch: false });
  const drain = () => runCaptureWorker(dataDir, { call, attempts: 1 });
  return { dir, cfg, ctx, file, configPath, messages, sessions, writes, creates, commits, call, queue, drain,
    loseAck: () => { loseAck = true; }, offline: (value) => { offline = value; } };
}

test("capture is opt-in and host options cannot replace the connection", async (t) => {
  const f = await fixture(t);
  const cfg = await loadConfig({ OPENVIKING_CLI_CONFIG_FILE: f.configPath }, { autoCapture: false, baseUrl: "http://wrong", apiKey: "wrong", nativeAgents: ["pi"] });
  assert.equal(cfg.autoCapture, false); assert.equal(cfg.baseUrl, f.file.url); assert.equal(cfg.apiKey, "fixture");
  assert.deepEqual(cfg.nativeAgents, ["pi"]);
});

test("duplicate snapshots, concurrent workers and process restarts send one copy", async (t) => {
  const f = await fixture(t);
  await Promise.all([f.queue(), f.queue()]);
  const files = await readdir(join(captureDirectory(f.cfg, f.ctx), "pending"));
  assert.equal(files.length, 1);
  await Promise.all([f.drain(), f.drain()]);
  await f.queue(); await f.drain();
  assert.equal(f.writes.length, 1); assert.equal(f.writes[0].length, 2);
  assert.equal(f.writes[0][0].peer_id, "test-project");
  assert.equal(f.creates[0].auto_commit_policy, null);
  assert.equal(f.commits.length, 1);
});

test("repeated text in a later user turn is new content; only the delta is sent", async (t) => {
  const f = await fixture(t);
  await f.queue(); await f.drain();
  await f.queue([...f.messages, ...f.messages]); await f.drain();
  assert.deepEqual(f.writes.map((w) => w.length), [2, 2]);
  assert.notEqual(f.writes[0][0].source_message_ids[0], f.writes[1][0].source_message_ids[0]);
});

test("a lost write acknowledgement is reconciled without resending, including after archive", async (t) => {
  const f = await fixture(t);
  f.loseAck(); await f.queue(); await f.drain();
  assert.equal(f.writes.length, 1);
  for (const s of f.sessions.values()) { s.archive = s.rows; s.rows = []; }
  await f.drain();
  assert.equal(f.writes.length, 1);
  assert.deepEqual(await readdir(join(captureDirectory(f.cfg, f.ctx), "pending")), []);
});

test("unknown delivery outcome stays pending instead of risking duplicate messages", async (t) => {
  const f = await fixture(t);
  await f.queue();
  let writes = 0;
  const call = async (...args) => {
    if (args[1].endsWith("/messages/batch")) { writes++; throw Object.assign(new Error("unknown outcome"), { code: "timeout" }); }
    return f.call(...args);
  };
  await runCaptureWorker(f.cfg.dataDir, { call, attempts: 1 });
  await runCaptureWorker(f.cfg.dataDir, { call, attempts: 1 });
  assert.equal(writes, 1);
  assert.equal((await readdir(join(captureDirectory(f.cfg, f.ctx), "pending"))).length, 1);
});

test("offline data remains queued; progress loss recovers from server provenance", async (t) => {
  const f = await fixture(t);
  await f.queue(); f.offline(true); await f.drain(); assert.equal(f.writes.length, 0);
  f.offline(false); await f.drain(); assert.equal(f.writes.length, 1);
  await rm(join(captureDirectory(f.cfg, f.ctx), "state"), { recursive: true });
  await f.queue(); await f.drain(); assert.equal(f.writes.length, 1);
});

test("changed delivered history pauses rather than copying the old conversation again", async (t) => {
  const f = await fixture(t);
  await f.queue(); await f.drain();
  await f.queue([{ role: "user", content: "Rewritten first question" }, f.messages[1]]); await f.drain();
  assert.equal(f.writes.length, 1);
  const state = JSON.parse(await readFile(join(captureDirectory(f.cfg, f.ctx), "state", `${connectionScope(f.cfg, f.ctx)}.json`), "utf8"));
  assert.equal(state.blocked, "history_changed");
});

test("native plugin markers surrender recall and capture for the whole session", async (t) => {
  const f = await fixture(t);
  await f.queue();
  const body = { messages: [{ role: "system", content: '<openviking-context source="session-start">native memory</openviking-context>' }, ...f.messages] };
  assert.equal(await nativeOwner(f.cfg, f.ctx, body), true);
  await f.drain(); assert.equal(f.writes.length, 0); assert.equal(f.creates.length, 0);
  assert.equal(await nativeOwner(f.cfg, f.ctx, { messages: f.messages }), true);
  let reported;
  assert.equal(await recallRequest({ model: "m", messages: [f.messages[0]] }, { ...f.ctx, query: "What is my preference?", report: (code) => { reported = code; } }, { OPENVIKING_CLI_CONFIG_FILE: f.configPath }), undefined);
  assert.equal(reported, "skipped");
  assert.equal(await nativeOwner(f.cfg, { ...f.ctx, session: "another-session" }, { messages: f.messages }), false);
});

test("native agent overrides and actual connection identities isolate ownership", async (t) => {
  const f = await fixture(t);
  assert.equal(await nativeOwner({ ...f.cfg, nativeAgents: ["pi"] }, f.ctx, {}), true);
  await nativeOwner(f.cfg, f.ctx, { system: "<relevant-memories>native</relevant-memories>" });
  assert.equal(await nativeOwner({ ...f.cfg, extraHeaders: { "X-Tenant": "other" } }, f.ctx, {}), false);
  assert.equal(await nativeOwner(f.cfg, { ...f.ctx, agent: "claude" }, {}), false);
});

test("quoted documentation is data, and injected memory is removed from capture", async (t) => {
  const f = await fixture(t);
  const quote = '<openviking-context source="documentation">example</openviking-context>';
  assert.equal(await nativeOwner(f.cfg, f.ctx, { messages: [f.messages[0], { role: "assistant", content: quote }] }), false);
  const rows = captureMessages([{ role: "user", content: `My actual question\n${quote}` }, f.messages[1]], "chat", f.cfg);
  assert.match(JSON.stringify(rows), /My actual question/); assert.doesNotMatch(JSON.stringify(rows), /example|openviking-context/);
});

test("tool continuations wait for a final response and preserve complete call/result evidence", async (t) => {
  const f = await fixture(t);
  const call = { role: "assistant", content: "Checking", tool_calls: [{ id: "tool-1", type: "function", function: { name: "lookup", arguments: '{"q":"test"}' } }] };
  const result = { role: "tool", tool_call_id: "tool-1", content: "Lookup result" };
  assert.equal(captureMessages([f.messages[0], call], "chat", f.cfg), null);
  const rows = captureMessages([f.messages[0], call, result, f.messages[1]], "chat", f.cfg);
  const tools = rows.flatMap((row) => row.parts).filter((p) => p.type === "tool");
  assert.equal(tools.length, 2); assert.equal(tools[0].tool_id, tools[1].tool_id);
  assert.equal(tools[1].tool_output, "Lookup result");
  assert.equal(completedReply({ choices: [{ finish_reason: "tool_calls", message: call }] }, "chat"), null);
  assert.equal(completedReply({ choices: [{ finish_reason: "length", message: f.messages[1] }] }, "chat"), null);
  await finishCapture({ choices: [{ finish_reason: "stop", message: f.messages[1] }] }, { ...f.ctx, success: false }, f.cfg);
  assert.equal(f.writes.length, 0);
});

for (const protocol of ["chat", "responses", "anthropic", "gemini"]) {
  test(`${protocol} captures user and final assistant text without reasoning`, async (t) => {
    const f = await fixture(t);
    let messages = f.messages;
    let reply = { choices: [{ finish_reason: "stop", message: f.messages[1] }] };
    if (protocol === "responses") { messages = f.messages.map((m) => ({ type: "message", role: m.role, content: [{ type: m.role === "user" ? "input_text" : "output_text", text: m.content }] })); reply = { status: "completed", output: [messages[1]] }; }
    if (protocol === "anthropic") { messages = f.messages.map((m) => ({ role: m.role, content: [{ type: "text", text: m.content }] })); messages[1].content.unshift({ type: "thinking", thinking: "private reasoning" }); reply = { stop_reason: "end_turn", content: messages[1].content }; }
    if (protocol === "gemini") { messages = f.messages.map((m) => ({ role: m.role === "assistant" ? "model" : "user", parts: [{ text: m.content }] })); messages[1].parts.unshift({ thought: true, text: "private reasoning" }); reply = { candidates: [{ finishReason: "STOP", content: messages[1] }] }; }
    assert.ok(completedReply(reply, protocol));
    const rows = captureMessages(messages, protocol, f.cfg);
    assert.equal(rows.length, 2); assert.equal(rows[0].role, "user"); assert.equal(rows[1].role, "assistant");
    assert.doesNotMatch(JSON.stringify(rows), /private reasoning/);
    await f.queue(messages, { ...f.ctx, protocol }); await f.drain(); assert.equal(f.writes.length, 1);
  });
}

test("unfinished exchanges and empty replies never become capture jobs", async (t) => {
  const f = await fixture(t);
  assert.equal(await f.queue([f.messages[0]]), false);
  assert.equal(completedReply({ choices: [{ finish_reason: "stop", message: { role: "assistant", content: "" } }] }, "chat"), null);
  assert.equal(completedReply({ status: "completed", output: [] }, "responses"), null);
});

test("turning capture off in the file pauses queued work", async (t) => {
  const f = await fixture(t); await f.queue();
  await writeFile(f.configPath, JSON.stringify({ ...f.file, plugin: { magpie: { ...f.file.plugin.magpie, autoCapture: false } } }));
  await f.drain(); assert.equal(f.writes.length, 0);
});

test("credential rotation cannot send queued text under a new identity", async (t) => {
  const f = await fixture(t); await f.queue();
  await writeFile(f.configPath, JSON.stringify({ ...f.file, api_key: "another-person" }));
  await f.drain(); assert.equal(f.writes.length, 0); assert.equal(f.creates.length, 0);
});

for (const protocol of ["anthropic", "responses", "gemini"]) {
  test(`${protocol} preserves complete tool evidence`, async (t) => {
    const f = await fixture(t);
    let messages;
    if (protocol === "anthropic") messages = [f.messages[0], { role: "assistant", content: [{ type: "tool_use", id: "c1", name: "lookup", input: { query: "q" } }] }, { role: "user", content: [{ type: "tool_result", tool_use_id: "c1", content: "found" }] }, f.messages[1]];
    if (protocol === "responses") messages = [f.messages[0], { type: "function_call", call_id: "c1", name: "lookup", arguments: '{"query":"q"}' }, { type: "function_call_output", call_id: "c1", output: "found" }, f.messages[1]];
    if (protocol === "gemini") messages = [{ role: "user", parts: [{ text: "question" }] }, { role: "model", parts: [{ functionCall: { name: "lookup", args: { query: "q" } } }] }, { role: "user", parts: [{ functionResponse: { name: "lookup", response: { value: "found" } } }] }, { role: "model", parts: [{ text: "answer" }] }];
    const rows = captureMessages(messages, protocol, f.cfg);
    assert.ok(rows); const tools = rows.flatMap((m) => m.parts).filter((p) => p.type === "tool");
    assert.equal(tools.length, 2); assert.equal(tools[0].tool_id, tools[1].tool_id);
    assert.match(tools[1].tool_output, /found/);
  });
}

test("a transport retry with a different generated answer does not duplicate or poison the session", async (t) => {
  const f = await fixture(t); await f.queue(); await f.drain();
  await f.queue([f.messages[0], { role: "assistant", content: "Different answer from a retried model request" }]);
  await f.drain(); assert.equal(f.writes.length, 1);
  await f.queue([...f.messages, { role: "user", content: "A new question" }, { role: "assistant", content: "A new answer" }]);
  await f.drain(); assert.equal(f.writes.length, 2);
});

test("global capture settings do not opt a newly installed gateway into capture", async (t) => {
  const f = await fixture(t);
  await writeFile(f.configPath, JSON.stringify({ ...f.file, plugin: { autoCapture: true, magpie: { dataDir: f.cfg.dataDir } } }));
  assert.equal((await loadConfig({ OPENVIKING_CLI_CONFIG_FILE: f.configPath })).autoCapture, false);
  assert.equal((await loadConfig({ OPENVIKING_CLI_CONFIG_FILE: f.configPath }, { autoCapture: true })).autoCapture, true);
});

test("lost local progress does not append an alternate retry answer", async (t) => {
  const f = await fixture(t); await f.queue(); await f.drain();
  await rm(join(captureDirectory(f.cfg, f.ctx), "state"), { recursive: true });
  await f.queue([f.messages[0], { role: "assistant", content: "An alternate answer" }]);
  await f.drain(); assert.equal(f.writes.length, 1);
});

test("a lost commit acknowledgement is reconciled without a duplicate extraction request", async (t) => {
  const f = await fixture(t); await f.queue();
  let lost = true;
  const call = async (...args) => {
    const result = await f.call(...args);
    if (args[1].endsWith("/commit") && lost) { lost = false; throw Object.assign(new Error("lost commit acknowledgement"), { code: "timeout" }); }
    return result;
  };
  await runCaptureWorker(f.cfg.dataDir, { call, attempts: 1 });
  await runCaptureWorker(f.cfg.dataDir, { call, attempts: 1 });
  assert.equal(f.writes.length, 1); assert.equal(f.commits.length, 1);
  assert.deepEqual(await readdir(join(captureDirectory(f.cfg, f.ctx), "pending")), []);
});

test("invalid native-agent ownership settings cannot silently reactivate automatic work", async (t) => {
  const f = await fixture(t);
  await assert.rejects(loadConfig({ OPENVIKING_CLI_CONFIG_FILE: f.configPath }, { nativeAgents: "pi" }), /invalid/);
});

test("capture never joins an unknown existing history after enabling or switching identity", async (t) => {
  const f = await fixture(t);
  await prepareCapture({ messages: [...f.messages, { role: "user", content: "Continue my old private conversation" }] }, f.ctx, f.cfg, {});
  assert.equal(f.ctx.state.capture, undefined);
  assert.equal(f.writes.length, 0);
});

const titleUtilityPrompt = "<session>Review code</session>\n\nWrite the title in the predominant language of the session — a stray word or code token in another language doesn't change it, and neither does the English of these instructions.";
async function queuedSnapshots(f) {
  const path = join(captureDirectory(f.cfg, f.ctx), "pending");
  return Promise.all((await readdir(path).catch(() => [])).filter((n) => n.endsWith(".json"))
    .map(async (name) => JSON.parse(await readFile(join(path, name), "utf8"))));
}
const finalChat = (text) => ({ choices: [{ finish_reason: "stop", message: { role: "assistant", content: text } }] });

for (const protocol of ["chat", "anthropic", "responses", "gemini"]) {
  test(`${protocol} cannot bind across pre-existing assistant text`, async (t) => {
    const f = await fixture(t); f.ctx.protocol = protocol;
    const messages = protocol === "gemini" ? [{ role: "model", parts: [{ text: "OLD_PRIVATE_TEXT" }] }, { role: "user", parts: [{ text: "New question" }] }]
      : [{ role: "assistant", content: "OLD_PRIVATE_TEXT" }, { role: "user", content: "New question" }];
    const body = protocol === "gemini" ? { contents: messages } : protocol === "responses" ? { store: false, input: messages } : { messages };
    const ctx = { ...f.ctx, protocol, state: {} };
    await prepareCapture(body, ctx, f.cfg, {});
    assert.equal(ctx.state.capture, undefined);
    assert.deepEqual(await queuedSnapshots(f), []);
    assert.deepEqual(await readdir(captureDirectory(f.cfg, ctx)).catch(() => []), []);
  });
}

test("historical title exchanges stay excluded from recovery, final snapshots and delivery", async (t) => {
  const f = await fixture(t);
  await prepareCapture({ messages: [f.messages[0]] }, f.ctx, f.cfg, {});
  await finishCapture(finalChat(f.messages[1].content), { ...f.ctx, success: true }, f.cfg);
  const titleCtx = { ...f.ctx, state: {} };
  await prepareCapture({ messages: [...f.messages, { role: "user", content: titleUtilityPrompt }] }, titleCtx, f.cfg, {});
  assert.equal(titleCtx.state.capture, undefined);
  const next = { messages: [...f.messages, { role: "user", content: titleUtilityPrompt },
    { role: "assistant", content: '{"title":"INTERNAL_TITLE_REPLY"}' }, { role: "user", content: "Next real question" }] };
  const ctx = { ...f.ctx, state: {} };
  await prepareCapture(next, ctx, f.cfg, {});
  assert.doesNotMatch(JSON.stringify(await queuedSnapshots(f)), /Write the title|INTERNAL_TITLE_REPLY/);
  await finishCapture(finalChat("Next real answer"), { ...ctx, success: true }, f.cfg);
  assert.doesNotMatch(JSON.stringify(await queuedSnapshots(f)), /Write the title|INTERNAL_TITLE_REPLY/);
  await f.drain();
  assert.deepEqual(f.writes.map((batch) => batch.length), [2, 2]);
  assert.doesNotMatch(JSON.stringify(f.writes), /Write the title|INTERNAL_TITLE_REPLY/);
});

for (const protocol of ["chat", "anthropic", "responses", "gemini"]) {
  test(`${protocol} persists a known truncated turn across recovery and final snapshots`, async (t) => {
    const f = await fixture(t); f.ctx.protocol = protocol;
    const messages = protocol === "gemini" ? [{ role: "user", parts: [{ text: "First question" }] }]
      : [{ role: "user", content: "First question" }];
    const wire = (rows) => protocol === "gemini" ? { contents: rows } : protocol === "responses" ? { store: false, input: rows } : { messages: rows };
    const failed = protocol === "gemini" ? { role: "model", parts: [{ text: "KNOWN_TRUNCATED_REPLY" }] }
      : { role: "assistant", content: "KNOWN_TRUNCATED_REPLY" };
    const partial = protocol === "chat" ? { choices: [{ finish_reason: "length", message: failed }] }
      : protocol === "anthropic" ? { stop_reason: "max_tokens", content: [{ type: "text", text: failed.content }] }
        : protocol === "responses" ? { status: "incomplete", output: [failed] }
          : { candidates: [{ finishReason: "MAX_TOKENS", content: failed }] };
    const ctx = { ...f.ctx, protocol, state: {} };
    await prepareCapture(wire(messages), ctx, f.cfg, {});
    await finishCapture(partial, { ...ctx, success: true }, f.cfg);
    assert.deepEqual(await queuedSnapshots(f), []);
    const question = protocol === "gemini" ? { role: "user", parts: [{ text: "A later question" }] } : { role: "user", content: "A later question" };
    const nextCtx = { ...ctx, state: {} };
    await prepareCapture(wire([...messages, failed, question]), nextCtx, f.cfg, {});
    assert.deepEqual(await queuedSnapshots(f), []);
    const final = protocol === "chat" ? finalChat("Later valid answer")
      : protocol === "anthropic" ? { stop_reason: "end_turn", content: [{ type: "text", text: "Later valid answer" }] }
        : protocol === "responses" ? { status: "completed", output: [{ role: "assistant", content: [{ type: "output_text", text: "Later valid answer" }] }] }
          : { candidates: [{ finishReason: "STOP", content: { role: "model", parts: [{ text: "Later valid answer" }] } }] };
    await finishCapture(final, { ...nextCtx, success: true }, f.cfg);
    const snapshots = await queuedSnapshots(f);
    assert.equal(snapshots.length, 1);
    assert.equal(snapshots[0].messages.length, 2);
    assert.doesNotMatch(JSON.stringify(snapshots), /KNOWN_TRUNCATED_REPLY|First question/);
  });
}

test("known failed callbacks stay rejected but a successful retry can explicitly resolve them", async (t) => {
  const f = await fixture(t);
  await prepareCapture({ messages: [f.messages[0]] }, f.ctx, f.cfg, {});
  await finishCapture(finalChat("Unsuccessful result"), { ...f.ctx, success: false }, f.cfg);
  const nextCtx = { ...f.ctx, state: {} };
  await prepareCapture({ messages: [...f.messages, { role: "user", content: "Next question" }] }, nextCtx, f.cfg, {});
  assert.deepEqual(await queuedSnapshots(f), []);
  await finishCapture(finalChat("Successful retry"), { ...f.ctx, success: true }, f.cfg);
  const snapshots = await queuedSnapshots(f);
  assert.equal(snapshots.length, 1); assert.match(JSON.stringify(snapshots), /Successful retry/);
});

test("native ownership immediately removes pending raw text without a running worker", async (t) => {
  const f = await fixture(t); await f.queue();
  assert.equal((await queuedSnapshots(f)).length, 1);
  await prepareCapture({ messages: [{ role: "system", content: "<openviking-context>\nNative memory\n</openviking-context>" }, f.messages[0]] }, f.ctx, f.cfg, {});
  assert.equal(f.ctx.state.nativeOwner, true);
  assert.deepEqual(await queuedSnapshots(f), []);
  assert.equal(await f.queue(), false);
  await f.drain(); assert.equal(f.writes.length, 0);
});

test("native cleanup coordinates with an already reading delivery worker", async (t) => {
  const f = await fixture(t); await f.queue(); let surrendered = false;
  await runCaptureWorker(f.cfg.dataDir, { attempts: 1, call: async (...args) => {
    if (!surrendered) {
      surrendered = true;
      await nativeOwner({ ...f.cfg, nativeAgents: [f.ctx.agent] }, f.ctx, {});
    }
    return f.call(...args);
  } });
  assert.equal(surrendered, true);
  assert.equal(f.writes.length, 0); assert.equal(f.commits.length, 0); assert.equal(f.creates.length, 0);
  assert.deepEqual(await queuedSnapshots(f), []);
});

test("a queued snapshot is discarded when a later callback proves its exchange failed", async (t) => {
  const f = await fixture(t);
  await prepareCapture({ messages: [f.messages[0]] }, f.ctx, f.cfg, {});
  await f.queue();
  await finishCapture(finalChat("Unsuccessful callback"), { ...f.ctx, success: false }, f.cfg);
  await f.drain();
  assert.equal(f.writes.length, 0); assert.equal(f.commits.length, 0);
  assert.deepEqual(await queuedSnapshots(f), []);
});


test("a genuinely lost completion callback can still recover a confirmed complete history", async (t) => {
  const f = await fixture(t);
  await prepareCapture({ messages: [f.messages[0]] }, f.ctx, f.cfg, {});
  const ctx = { ...f.ctx, state: {} };
  await prepareCapture({ messages: [...f.messages, { role: "user", content: "Next real question" }] }, ctx, f.cfg, {});
  const snapshots = await queuedSnapshots(f);
  assert.equal(snapshots.length, 1); assert.equal(snapshots[0].messages.length, 2);
  await finishCapture(finalChat("Final answer"), { ...ctx, success: true }, f.cfg);
  await f.drain(); assert.deepEqual(f.writes.map((batch) => batch.length), [2, 2]);
});

test("tool-call continuations are not persisted as failed user exchanges", async (t) => {
  const f = await fixture(t);
  await prepareCapture({ messages: [f.messages[0]] }, f.ctx, f.cfg, {});
  const tool = { role: "assistant", content: null, tool_calls: [{ id: "tool", type: "function", function: { name: "lookup", arguments: "{}" } }] };
  await finishCapture({ choices: [{ finish_reason: "tool_calls", message: tool }] }, { ...f.ctx, success: true }, f.cfg);
  const ctx = { ...f.ctx, state: {} };
  await prepareCapture({ messages: [f.messages[0], tool, { role: "tool", tool_call_id: "tool", content: "Found" }] }, ctx, f.cfg, {});
  await finishCapture(finalChat("Complete answer"), { ...ctx, success: true }, f.cfg);
  const snapshots = await queuedSnapshots(f);
  assert.equal(snapshots.length, 1); assert.equal(snapshots[0].messages.length, 4);
});

test("a successful retry cannot authorize an older worker's invalidated snapshot", async (t) => {
  const f = await fixture(t);
  await prepareCapture({ messages: [f.messages[0]] }, f.ctx, f.cfg, {});
  await f.queue(); let resolved = false;
  await runCaptureWorker(f.cfg.dataDir, { attempts: 1, call: async (...args) => {
    if (!resolved) {
      resolved = true;
      await finishCapture(finalChat("Bad result"), { ...f.ctx, success: false }, f.cfg);
      await finishCapture(finalChat("Successful replacement"), { ...f.ctx, success: true }, f.cfg);
    }
    return f.call(...args);
  } });
  assert.equal(f.writes.length, 0);
  // The stale worker must not unlink a replacement published at the same path.
  assert.equal((await queuedSnapshots(f)).length, 1);
  await f.drain();
  assert.equal(f.writes.length, 1); assert.match(JSON.stringify(f.writes), /Successful replacement/);
  assert.doesNotMatch(JSON.stringify(f.writes), /I understand your project preference/);
});

test("an invalidated append intent that was never sent does not block a successful retry", async (t) => {
  const fs = await import("node:fs/promises");
  const f = await fixture(t);
  await prepareCapture({ messages: [f.messages[0]] }, f.ctx, f.cfg, {}); await f.queue();
  const target = join(captureDirectory(f.cfg, f.ctx), "state", `${connectionScope(f.cfg, f.ctx)}.json`);
  const original = fs.default.rename;
  let interrupted = false;
  const replacement = mock.method(fs.default, "rename", async (...args) => {
    await original(...args);
    if (args[1] === target && !interrupted && JSON.parse(await readFile(target, "utf8")).intent) {
      interrupted = true;
      await finishCapture(finalChat("Rejected before send"), { ...f.ctx, success: false }, f.cfg);
      await finishCapture(finalChat("Successful retry after rejection"), { ...f.ctx, success: true }, f.cfg);
    }
  });
  syncBuiltinESMExports();
  try {
    await f.drain();
    assert.equal(interrupted, true); assert.equal(f.writes.length, 0);
    assert.equal(JSON.parse(await readFile(target, "utf8")).intent, null);
    await f.drain();
    assert.equal(f.writes.length, 1);
    assert.match(JSON.stringify(f.writes), /Successful retry after rejection/);
  } finally { replacement.mock.restore(); syncBuiltinESMExports(); }
});


for (const status of [404, 405]) {
  test(`unsupported batch HTTP ${status} falls back serially without duplicate delivery`, async (t) => {
    const f = await fixture(t);
    await f.queue();
    const call = async (...args) => {
      if (args[1].endsWith("/messages/batch")) throw Object.assign(new Error("unsupported"), { status });
      return f.call(...args);
    };
    await runCaptureWorker(f.cfg.dataDir, { call, attempts: 1 });
    await f.queue();
    await runCaptureWorker(f.cfg.dataDir, { call, attempts: 1 });
    assert.deepEqual(f.writes.map((rows) => rows.length), [1, 1]);
    assert.equal(f.commits.length, 1);
    assert.deepEqual(await readdir(join(captureDirectory(f.cfg, f.ctx), "pending")), []);
  });
}

test("serial fallback reconciles a lost acknowledgement before sending the remaining prefix", async (t) => {
  const f = await fixture(t);
  await f.queue();
  let lost = false;
  const call = async (...args) => {
    if (args[1].endsWith("/messages/batch")) throw Object.assign(new Error("unsupported"), { status: 405 });
    const result = await f.call(...args);
    if (args[1].endsWith("/messages") && !lost) {
      lost = true;
      throw Object.assign(new Error("lost acknowledgement"), { code: "timeout" });
    }
    return result;
  };
  await runCaptureWorker(f.cfg.dataDir, { call, attempts: 1 });
  assert.equal(f.writes.length, 1);
  assert.equal(f.commits.length, 0);
  await runCaptureWorker(f.cfg.dataDir, { call, attempts: 1 });
  assert.deepEqual(f.writes.map((rows) => rows.length), [1, 1]);
  assert.equal(f.commits.length, 1);
});

for (const change of ["native", "credentials", "disabled", "bypass", "bypassPattern"]) {
  for (const stage of ["append", "commit"]) {
    test(`worker rechecks ${change} configuration before ${stage}`, async (t) => {
      const f = await fixture(t);
      await f.queue();
      let changed = false;
      const call = async (...args) => {
        const result = await f.call(...args);
        const target = stage === "append" ? /^\/api\/v1\/sessions\/[^/]+$/ : /\/messages\/batch$/;
        if (!changed && target.test(args[1])) {
          changed = true;
          const file = structuredClone(f.file);
          if (change === "native") file.plugin.magpie.nativeAgents = ["pi"];
          if (change === "credentials") file.api_key = "rotated";
          if (change === "disabled") file.plugin.magpie.autoCapture = false;
          if (change === "bypass") file.plugin.magpie.bypassSession = true;
          if (change === "bypassPattern") file.plugin.magpie.bypassSessionPatterns = ["convers*"];
          await writeFile(f.configPath, JSON.stringify(file));
        }
        return result;
      };
      await runCaptureWorker(f.cfg.dataDir, { call, attempts: 1 });
      assert.equal(changed, true);
      assert.equal(f.writes.length, stage === "append" ? 0 : 1);
      assert.equal(f.commits.length, 0);
      // No unsent intent may prevent recovery if capture is later re-enabled.
      const scope = connectionScope(f.cfg, f.ctx);
      const state = JSON.parse(await readFile(join(captureDirectory(f.cfg, f.ctx), "state", `${scope}.json`), "utf8"));
      assert.equal(state.intent, null);
      assert.equal(state.commitIntent, null);
    });
  }
}


for (const bypass of [{ bypassSession: true }, { bypassSessionPatterns: ["convers*"] }, { bypassSessionPatterns: ["/fixt*"] }]) {
  test(`bypass ${JSON.stringify(bypass)} skips hook recall and capture`, async (t) => {
    const f = await fixture(t);
    await writeFile(f.configPath, JSON.stringify({ ...f.file, plugin: { magpie: { ...f.file.plugin.magpie, ...bypass } } }));
    const previous = process.env.OPENVIKING_CLI_CONFIG_FILE;
    process.env.OPENVIKING_CLI_CONFIG_FILE = f.configPath;
    t.after(() => {
      if (previous === undefined) delete process.env.OPENVIKING_CLI_CONFIG_FILE;
      else process.env.OPENVIKING_CLI_CONFIG_FILE = previous;
    });
    let reads = 0;
    t.mock.method(globalThis, "fetch", async () => { reads++; return Response.json({ status: "ok", result: { rendered: "Private context" } }); });
    const cfg = await loadConfig();
    const ctx = { ...f.ctx, query: f.messages[0].content, success: true };
    assert.equal(await onRequest({ messages: [f.messages[0]] }, ctx), undefined);
    await onResponse({ choices: [{ finish_reason: "stop", message: f.messages[1] }] }, ctx);
    assert.equal(reads, 0);
    assert.equal(ctx.state.capture, undefined);
    // Guard direct entrypoints too, including a callback prepared before bypass.
    assert.equal(await recallRequest({ messages: [f.messages[0]] }, ctx, {}, cfg), undefined);
    assert.equal(await queueCapture(cfg, ctx, f.messages, { launch: false }), false);
    const active = { ...ctx, state: { capture: { body: { messages: [f.messages[0]] }, scope: connectionScope(cfg, ctx) } } };
    await finishCapture({ choices: [{ finish_reason: "stop", message: f.messages[1] }] }, active, cfg);
    assert.equal(reads, 0);
    await assert.rejects(readdir(captureDirectory(cfg, ctx)), { code: "ENOENT" });
  });
}

test("bypass pauses existing snapshots until removed, without affecting other sessions", async (t) => {
  const f = await fixture(t);
  await f.queue();
  const other = { ...f.ctx, session: "another-conversation" };
  await f.queue(f.messages, other);
  await writeFile(f.configPath, JSON.stringify({ ...f.file, plugin: { magpie: { ...f.file.plugin.magpie, bypassSessionPatterns: ["convers*"] } } }));
  await f.drain();
  assert.equal(f.writes.length, 1);
  assert.equal(f.commits.length, 1);
  assert.equal((await readdir(join(captureDirectory(f.cfg, f.ctx), "pending"))).length, 1);
  assert.deepEqual(await readdir(join(captureDirectory(f.cfg, other), "pending")), []);
  await writeFile(f.configPath, JSON.stringify(f.file));
  await f.drain();
  assert.equal(f.writes.length, 2);
  assert.equal(f.commits.length, 2);
  assert.deepEqual(await readdir(join(captureDirectory(f.cfg, f.ctx), "pending")), []);
});
