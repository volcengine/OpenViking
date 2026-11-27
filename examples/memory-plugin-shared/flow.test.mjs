import assert from "node:assert/strict";
import { appendFileSync, chmodSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { runStage, toolCall } from "../agent-hook-plugin/lib/flow/index.mjs";
import { parseJsonl, resumeTurns } from "../agent-hook-plugin/lib/flow/turns.mjs";
import {
  readRequestBody,
  scrubOpenVikingEnv,
  withMockOpenViking,
  writeCredentialFiles,
  writeJson,
} from "./testing/support.mjs";

// A host as small as the contract allows: its transcript is JSONL of
// `{ role, text }`, a record's index is its turn id.
const host = {
  id: "flowtest",
  sessionPrefix: "ft-",
  events: {
    start: "start",
    prompt: "prompt",
    stop: "turnEnd",
    compact: "compactSoon",
    end: "end",
    tool: "toolCall",
  },
  budgets: { end: 20_000 },
  read: (event, payload) => ({
    sessionId: payload.session_id,
    prompt: payload.prompt,
    transcript: payload.transcript_path,
    resumed: payload.source === "resume" ? "resume" : "",
  }),
  decode: ({ text, cursor }) => resumeTurns(
    parseJsonl(text).map((record, index) => ({ role: record.role, turnId: String(index), content: record.text })),
    cursor,
  ),
  emit: (event, output) => output,
  tool: (payload) => (
    payload.tool_name === "session_history"
      ? { history: payload.tool_input }
      : { name: payload.tool_name, input: payload.tool_input }
  ),
};

const SESSION = "/api/v1/sessions/ft-s1";

/**
 * A session directory, a mock OpenViking and `run(event, extra)`. `answers`
 * maps "METHOD path-suffix" to a status or a function of the request body;
 * anything unlisted answers 200.
 */
async function withFlow(t, answers, fn, { env = {} } = {}) {
  const restore = scrubOpenVikingEnv();
  t.after(restore);
  const files = await writeCredentialFiles("ov-flow-", { ovcli: { url: "http://127.0.0.1:9", api_key: "k" }, ov: {} });
  t.after(() => rmSync(files.dir, { recursive: true, force: true }));
  const transcript = join(files.dir, "session.jsonl");
  writeFileSync(transcript, "");
  const seen = [];
  const handler = async (req, res) => {
    const path = new URL(req.url, "http://127.0.0.1").pathname;
    const body = await readRequestBody(req);
    seen.push({ call: `${req.method} ${path}`, body });
    const key = Object.keys(answers).find((entry) => `${req.method} ${path}`.endsWith(entry));
    const answer = typeof answers[key] === "function" ? answers[key](body) : answers[key];
    if (typeof answer === "number") return writeJson(res, { status: "error", error: { message: "no" } }, answer);
    return writeJson(res, { status: "ok", result: answer ?? {} });
  };
  await withMockOpenViking(handler, async (url) => {
    Object.assign(process.env, files.env, {
      OPENVIKING_URL: url,
      OPENVIKING_HOOK_STATE_DIR: join(files.dir, "hook-state"),
      OPENVIKING_PENDING_DIR: join(files.dir, "pending"),
      OPENVIKING_STATE_DIR: join(files.dir, "state"),
      ...env,
    });
    const say = (role, text) => appendFileSync(transcript, `${JSON.stringify({ role, text })}\n`);
    const run = (event, extra = {}) => runStage(host, event, {
      session_id: "s1",
      cwd: files.dir,
      transcript_path: transcript,
      ...extra,
    });
    const calls = (suffix) => seen.filter((entry) => entry.call.endsWith(suffix));
    const sentTexts = () => calls("/messages/batch")
      .flatMap((entry) => entry.body.messages)
      .map((message) => message.parts.map((part) => part.text).join(""));
    await fn({ run, say, seen, calls, sentTexts, dir: files.dir, transcript });
  });
}

test("a turn end sends only the turns past the cursor and hands the threshold commit to the server", async (t) => {
  await withFlow(t, {}, async ({ run, say, seen, calls, sentTexts }) => {
    say("user", "first question");
    say("assistant", "first answer");
    await run("stop");
    assert.deepEqual(sentTexts(), ["first question", "first answer"]);
    assert.equal(calls("/messages/batch")[0].body.messages[0].turn_id, "0");

    // The policy goes up once, every field named.
    assert.deepEqual(calls(`PATCH ${SESSION}/config`)[0].body.auto_commit_policy, {
      pending_token_threshold: 50000,
      message_count_threshold: 0,
      idle_timeout_seconds: 1800,
      keep_recent_count: 0,
      min_commit_interval_seconds: 0,
    });

    say("user", "second question");
    await run("stop");
    await run("stop");
    assert.deepEqual(sentTexts().slice(2), ["second question"]);
    assert.deepEqual(seen.map((entry) => entry.call.split("/").at(-1)), ["batch", "config", "batch"]);
  }, { env: { OPENVIKING_COMMIT_TOKEN_THRESHOLD: "50000" } });
});

test("a policy the server did not take is offered again with the next turn", async (t) => {
  let status = 503;
  await withFlow(t, { [`PATCH ${SESSION}/config`]: () => status }, async ({ run, say, calls }) => {
    say("user", "one");
    await run("stop");
    status = undefined;
    say("assistant", "two");
    await run("stop");
    say("user", "three");
    await run("stop");
    assert.equal(calls("/config").length, 2);
    assert.equal(calls("/commit").length, 0);
  });
});

test("a compaction and a session end commit what turn ends left uncommitted, once", async (t) => {
  await withFlow(t, {}, async ({ run, say, calls, sentTexts }) => {
    say("user", "question");
    say("assistant", "an answer that is still being written");
    await run("compact");
    // The last assistant message may still grow, so it waits.
    assert.deepEqual(sentTexts(), ["question"]);
    assert.equal(calls("/commit").length, 1);

    await run("stop");
    assert.deepEqual(sentTexts(), ["question", "an answer that is still being written"]);
    await run("end");
    await run("end");
    assert.equal(calls("/commit").length, 2);
    assert.equal(calls("/messages/batch").length, 2);
  });
});

test("a session end waits for the turn end that holds the session lock", async (t) => {
  await withFlow(t, {}, async ({ run, say, calls, dir }) => {
    say("user", "question");
    await run("stop");
    const lock = join(dir, "hook-state", "flowtest", "s1.json.lock");
    mkdirSync(lock);
    const ending = run("end");
    await new Promise((resolve) => setTimeout(resolve, 200));
    assert.equal(calls("/commit").length, 0);
    rmSync(lock, { recursive: true });
    await ending;
    assert.equal(calls("/commit").length, 1);
  });
});

test("requests share the event's budget: once it is spent the turns are queued, not lost", async (t) => {
  const slow = { ...host, budgets: { stop: 1500 } };
  await withFlow(t, {}, async ({ say, calls, dir, transcript }) => {
    say("user", "question");
    const lock = join(dir, "hook-state", "flowtest", "s1.json.lock");
    mkdirSync(lock, { recursive: true });
    setTimeout(() => rmSync(lock, { recursive: true }), 700);
    await runStage(slow, "stop", { session_id: "s1", cwd: dir, transcript_path: transcript });
    assert.equal(calls("/messages/batch").length, 0);
    assert.equal(readdirSync(join(dir, "pending")).filter((name) => name.endsWith(".json")).length, 1);
  });
});

test("autoCommitOnCompact off captures before a compaction without committing", async (t) => {
  await withFlow(t, {}, async ({ run, say, calls, sentTexts }) => {
    say("user", "question");
    await run("compact");
    assert.deepEqual(sentTexts(), ["question"]);
    assert.equal(calls("/commit").length, 0);
  }, { env: { OPENVIKING_AUTO_COMMIT_ON_COMPACT: "0" } });
});

test("an unreachable server queues the turns, and the next session start replays them", async (t) => {
  await withFlow(t, {}, async ({ run, say, calls, dir }) => {
    say("user", "kept while offline");
    const online = process.env.OPENVIKING_URL;
    process.env.OPENVIKING_URL = "http://127.0.0.1:9";
    await run("stop");
    assert.equal(readdirSync(join(dir, "pending")).filter((name) => name.endsWith(".json")).length, 1);

    process.env.OPENVIKING_URL = online;
    await run("stop");
    assert.equal(calls("/messages/batch").length, 0, "a queued turn is not sent a second time");
    await run("start");
    assert.equal(calls("/messages").length, 1);
  });
});

test("refused credentials hold the cursor; refused content does not block what follows", async (t) => {
  let status = 401;
  await withFlow(t, { "/messages/batch": () => status }, async ({ run, say, calls }) => {
    say("user", "one");
    await run("stop");
    await run("stop");
    assert.equal(calls("/messages/batch").length, 2, "nothing was acknowledged while the key was refused");

    status = 422;
    await run("stop");
    say("user", "two");
    status = undefined;
    await run("stop");
    const last = calls("/messages/batch").at(-1).body.messages;
    assert.deepEqual(last.map((message) => message.parts[0].text), ["two"]);
  });
});

test("a transcript that cannot be read acknowledges nothing", async (t) => {
  await withFlow(t, {}, async ({ run, say, calls, transcript, sentTexts }) => {
    say("user", "one");
    chmodSync(transcript, 0o000);
    await run("stop");
    assert.equal(calls("/messages/batch").length, 0);
    chmodSync(transcript, 0o600);
    await run("stop");
    assert.deepEqual(sentTexts(), ["one"]);

    await run("stop", { transcript_path: undefined });
    assert.equal(calls("/messages/batch").length, 1);
  });
});

test("capture off, the plugin off and a bypassed session send nothing", async (t) => {
  for (const env of [
    { OPENVIKING_AUTO_CAPTURE: "0" },
    { OPENVIKING_MEMORY_ENABLED: "0" },
    { OPENVIKING_BYPASS_SESSION: "1" },
  ]) {
    await t.test(Object.keys(env)[0], async (subtest) => {
      await withFlow(subtest, {}, async ({ run, say, seen }) => {
        say("user", "one");
        await run("stop");
        await run("end");
        assert.deepEqual(seen.filter((entry) => !entry.call.startsWith("GET")), []);
      }, { env });
    });
  }
});

test("a prompt is recalled once, and a short or filtered one is not searched", async (t) => {
  const recall = { rendered: "what OpenViking remembers", entries: [], stats: {} };
  await withFlow(t, { "/search/search": recall, "/search/recall": recall }, async ({ run, calls }) => {
    const searches = () => calls("/search/search").length + calls("/search/recall").length;
    const first = await run("prompt", { prompt: "how did we set up the build?" });
    assert.match(first.context, /what OpenViking remembers/);
    const count = searches();
    assert.ok(count > 0);

    assert.deepEqual(await run("prompt", { prompt: "how did we set up the build?" }), {});
    assert.deepEqual(await run("prompt", { prompt: "ok" }), {});
    assert.deepEqual(await run("prompt", { prompt: "/status please show it" }), {});
    assert.equal(searches(), count);
  }, { env: { OPENVIKING_RECALL_QUERY_FILTERS: "d#^/#", OPENVIKING_MIN_QUERY_LENGTH: "3" } });
});

test("the note about the session's full record waits for an event that reaches the model", async (t) => {
  await withFlow(t, {}, async ({ run, say }) => {
    say("user", "question");
    await run("compact");
    const afterCompact = await run("prompt", { prompt: "what were we doing?" });
    assert.match(afterCompact.context, /source="session-history"/);
    assert.match(afterCompact.context, /what the compaction summary replaced/);
    assert.match(afterCompact.context, /ft-s1/);
    const next = await run("prompt", { prompt: "and what comes after that?" });
    assert.doesNotMatch(next.context || "", /session-history/);

    // A resume delivers its own note, and only when none was sent before.
    assert.doesNotMatch((await run("start", { source: "resume" })).context || "", /session-history/);
  });
  await withFlow(t, {}, async ({ run }) => {
    const resumed = await run("start", { source: "resume" });
    assert.match(resumed.context, /anything an earlier compaction summarized/);
  });
});

test("a tool call is judged from its payload alone", () => {
  const denied = toolCall(host, { tool_name: "Read", tool_input: { file_path: "viking://user/me/memories/a.md" } });
  assert.match(denied.deny, /OpenViking MCP read/);
  const noticed = toolCall(host, { tool_name: "Bash", tool_input: { command: "cat viking://user/me/a.md" } });
  assert.match(noticed.notice, /ignore this notice/);
  assert.deepEqual(toolCall(host, { tool_name: "Read", tool_input: { file_path: "/tmp/a.md" } }), {});

  const history = { session_id: "s1", tool_name: "session_history", tool_input: { action: "overview" } };
  assert.deepEqual(toolCall(host, history), { input: { action: "overview", session: "ft-s1" } });
  assert.deepEqual(toolCall(host, { ...history, tool_input: { action: "overview", session: "other" } }), {});
});

test("resumeTurns holds the cursor at a turn id until every message of it is acknowledged", () => {
  const all = [
    { role: "user", turnId: "t1", content: "q" },
    { role: "assistant", turnId: "t1", content: "a" },
    { role: "user", turnId: "t2", content: "q2" },
  ];
  const first = resumeTurns(all, {});
  assert.equal(first.turns.length, 3);
  const partial = first.cursorAfter(1);
  assert.equal(partial.lastTurnId, null);
  assert.deepEqual(resumeTurns(all, partial).turns.map((turn) => turn.content), ["a", "q2"]);
  assert.equal(first.cursorAfter(2).lastTurnId, "t1");

  // A rewritten transcript loses the turn the cursor names; the recent keys
  // still keep its turns from being sent again.
  const done = first.cursorAfter(3);
  assert.deepEqual(resumeTurns(all.slice(0, 2), done).turns, []);
});
