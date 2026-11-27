import assert from "node:assert/strict";
import { appendFileSync, chmodSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { turnMessages } from "../agent-hook-plugin/lib/flow/capture.mjs";
import { mayDetach, runStage, toolCall } from "../agent-hook-plugin/lib/flow/index.mjs";
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
async function withFlow(t, answers, fn, { env = {}, as = host } = {}) {
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
    const run = (event, extra = {}) => runStage(as, event, {
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

  // A host that no longer hands the turn over after the compaction sends it now.
  await withFlow(t, {}, async ({ run, say, sentTexts }) => {
    say("user", "question");
    say("assistant", "an answer");
    await run("compact");
    assert.deepEqual(sentTexts(), ["question", "an answer"]);
  }, { as: { ...host, facts: { compactionDropsTranscript: true } } });
});

test("events of one session handed over at once are written in the order they came", async (t) => {
  await withFlow(t, {}, async ({ run, say, sentTexts }) => {
    const order = [];
    for (const text of ["one", "two", "three", "four"]) {
      say(text === "one" ? "user" : "assistant", text);
      order.push(run("stop"));
    }
    await Promise.all(order);
    assert.deepEqual(sentTexts(), ["one", "two", "three", "four"]);
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

test("a note reaches decode until its turn is settled, with or without a transcript", async (t) => {
  // A host whose transcript may be absent and whose prompt only an event carries.
  const noted = {
    ...host,
    facts: { transcript: "optional" },
    read: (event, payload) => ({ ...host.read(event, payload), note: payload.prompt ? { prompt: payload.prompt } : undefined }),
    decode: ({ text, notes, cursor }) => resumeTurns(
      [...parseJsonl(text), ...notes.map((note) => ({ role: "user", text: note.prompt }))]
        .map((record) => ({ role: record.role, content: record.text })),
      cursor,
    ),
  };
  let refuse = 401;
  await withFlow(t, { "/messages/batch": () => refuse }, async ({ run, sentTexts, calls }) => {
    await run("prompt", { prompt: "only the event had this" });
    await run("stop", { transcript_path: "/nonexistent/session.jsonl" });
    refuse = undefined;
    await run("stop", { transcript_path: "/nonexistent/session.jsonl" });
    assert.deepEqual(sentTexts(), ["only the event had this", "only the event had this"]);

    // Settled, so the note is gone: a cursor that forgot it would not bring it back.
    await run("stop", { transcript_path: undefined });
    assert.equal(calls("/messages/batch").length, 2);
    assert.deepEqual(readdirSync(join(process.env.OPENVIKING_HOOK_STATE_DIR, "flowtest")).filter((name) => name.includes("notes")), []);
  }, { as: noted, env: { OPENVIKING_AUTO_RECALL: "0" } });
});

test("a child session is captured into a session of its own, from where an earlier version stopped", async (t) => {
  const parent = {
    ...host,
    read: (event, payload) => ({ ...host.read(event, payload), child: payload.agent_id ? `subagent:${payload.agent_id}` : "" }),
    legacyCursor: (sessionId, read) => (read.child ? { lastTurnId: "0" } : undefined),
  };
  await withFlow(t, {}, async ({ run, say, calls, sentTexts }) => {
    say("user", "sent by the old plugin");
    say("assistant", "the subagent's answer");
    await run("end", { agent_id: "a1" });
    assert.deepEqual(sentTexts(), ["the subagent's answer"]);
    assert.equal(calls("/api/v1/sessions/ft-s1__subagent-a1/commit").length, 1);

    // The parent has its own cursor.
    await run("stop");
    assert.equal(calls(`${SESSION}/messages/batch`).length, 1);
  }, { as: parent });

  await withFlow(t, {}, async ({ run, say, seen }) => {
    say("user", "one");
    await run("end", { agent_id: "a1" });
    assert.deepEqual(seen, []);
  }, { as: parent, env: { OPENVIKING_SKIP_SUBAGENT_SESSIONS: "1" } });

  // What the earlier version sent may never have been committed.
  await withFlow(t, {}, async ({ run, say, calls }) => {
    say("user", "sent by the old plugin");
    await run("end");
    assert.equal(calls("/messages/batch").length, 0);
    assert.equal(calls(`${SESSION}/commit`).length, 1);
  }, { as: { ...host, legacyCursor: () => ({ lastTurnId: "0" }) } });
});

test("keyword mode sends a batch only when the user said something to remember", async (t) => {
  await withFlow(t, {}, async ({ run, say, sentTexts }) => {
    say("user", "list the files");
    say("assistant", "done");
    await run("stop");
    assert.deepEqual(sentTexts(), []);

    say("user", "remember that the staging host is green-2");
    await run("stop");
    assert.deepEqual(sentTexts(), ["remember that the staging host is green-2"]);
  }, { env: { OPENVIKING_CAPTURE_MODE: "keyword" } });
});

test("a session opens with the profile, and a resume adds only what the model no longer holds", async (t) => {
  const answers = { "/content/read": "- prefers short answers", "/context": { latest_archive_overview: "earlier: the build was fixed" } };
  await withFlow(t, answers, async ({ run }) => {
    const first = await run("start");
    assert.match(first.context, /prefers short answers/);
    assert.doesNotMatch(first.context, /session-archive/);

    // Past the window in which a second start is taken for the same one.
    const stateFile = join(process.env.OPENVIKING_HOOK_STATE_DIR, "flowtest", "s1.json");
    writeFileSync(stateFile, JSON.stringify({ ...JSON.parse(readFileSync(stateFile, "utf8")), lastSessionStartAt: 0 }));
    const resumed = await run("start", { source: "resume" });
    assert.doesNotMatch(resumed.context, /prefers short answers/);
    assert.match(resumed.context, /earlier: the build was fixed/);
  }, { env: { OPENVIKING_RESUME_ARCHIVE_INJECT: "1" } });

  // Where a start cannot reach the model, the overview waits for the first prompt.
  await withFlow(t, answers, async ({ run }) => {
    assert.deepEqual(await run("start", { source: "resume" }), {});
    assert.match((await run("prompt", { prompt: "where were we with the build?" })).context, /earlier: the build was fixed/);
    assert.doesNotMatch((await run("prompt", { prompt: "and what about the tests then?" })).context || "", /earlier: the build/);
  }, { as: { ...host, facts: { profileAt: "firstPrompt" } }, env: { OPENVIKING_RESUME_ARCHIVE_INJECT: "1", OPENVIKING_AUTO_RECALL: "0" } });
});

test("the host is told what each event did", async (t) => {
  const told = [];
  const watched = {
    ...host,
    observe: (ctx, outcome) => { told.push([ctx.event, ctx.sessionId, { ...outcome }]); },
    // What the host adds to the context is part of what it is told.
    present: (ctx, context) => `${context}\n<card for="${ctx.event}"/>`,
  };
  const recall = { rendered: "what OpenViking remembers", entries: [], stats: {} };
  await withFlow(t, { "/search/search": recall, "/search/recall": recall }, async ({ run, say }) => {
    await run("prompt", { prompt: "how did we set up the build?" });
    say("user", "how did we set up the build?");
    await run("end");
    const [[, , prompt], [, session, end]] = told;
    assert.match(prompt.context, /what OpenViking remembers[\s\S]*<card for="prompt"\/>$/);
    assert.equal(typeof prompt.recall.stage, "string");
    assert.equal(session, "ft-s1");
    assert.deepEqual(end, { capture: { sent: 1, queued: 0, failed: 0, held: 0 }, committed: true });
  }, { as: watched });

  await withFlow(t, {}, async ({ run }) => {
    await run("start");
    assert.deepEqual(told.at(-1), ["start", "ft-s1", { skip: "bypass" }]);
  }, { as: watched, env: { OPENVIKING_BYPASS_SESSION: "1" } });
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

test("a write event detaches when the host allows it, and always where the host has no time for it", () => {
  const cfg = { autoCapture: true, writePathAsync: false };
  assert.equal(mayDetach(host, "end", cfg), false);
  assert.equal(mayDetach({ ...host, canDetach: true }, "end", cfg), true);
  assert.equal(mayDetach({ ...host, canDetach: true, syncEvents: ["end"] }, "end", cfg), false);
  assert.equal(mayDetach({ ...host, detachEvents: ["end"] }, "end", cfg), "always");
  assert.equal(mayDetach({ ...host, detachEvents: ["end"] }, "end", { ...cfg, autoCapture: false }), false);
  assert.equal(mayDetach({ ...host, canDetach: true }, "prompt", cfg), false);
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

test("a turn's id and its own time travel with its message", () => {
  const turn = { role: "user", content: "moved to Lisbon", turnId: "7", createdAt: "2023-02-12T16:02:00.000Z" };
  const [message] = turnMessages({ turns: [turn] }, {});
  assert.equal(message.turn_id, "7");
  assert.equal(message.created_at, "2023-02-12T16:02:00.000Z");
  assert.equal("created_at" in turnMessages({ turns: [{ role: "user", content: "hi" }] }, {})[0], false);
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
