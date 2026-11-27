import assert from "node:assert/strict";
import test from "node:test";

import {
  AGENT_TOOLS,
  createSealPolicy,
  envTurnsOff,
  isSealable,
  sealAttachment,
  sealingMode,
  sealNote,
  sealPrompt,
  sealToolResult,
  serverPost,
  settingUnknown,
  unsealCall,
  withheld,
} from "./prompt-seal.mjs";

const REF = "{{ov_privacy:alice:viking://~/privacy/auto/github-k3j2m8#/values/token}}";
const OLD_REF = "{{ov_privacy:alice:viking://~/privacy/credential/openai#/values/token}}";
const TOKEN = "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";

// A server answer for `text` with each [secret, reference, temporary] replaced.
function sealed(text, swaps) {
  const replaced = [];
  let out = text;
  for (const [secret, reference, temporary] of swaps) {
    const unit = text.indexOf(secret);
    const start = Array.from(text.slice(0, unit)).length;
    replaced.push({ start, end: start + Array.from(secret).length, reference, created: false, temporary });
    out = out.replace(secret, reference);
  }
  return { ok: true, status: 200, result: { revision: "r1", texts: [{ text: out, replaced }] } };
}

// The core's `post` for an answer written as `{ ok, status, result, error }`:
// status 0 is no answer at all (a network error, or a timeout when aborted).
function postOf(answer) {
  return async () => {
    if (!answer.ok && !answer.status) {
      if (answer.error?.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
      throw new Error(answer.error?.message || "");
    }
    const json = answer.ok ? { status: "ok", result: answer.result } : { status: "error", error: answer.error };
    return { status: answer.status, json };
  };
}

function recorder(answer) {
  const calls = [];
  const post = async (path, body) => {
    calls.push({ path, body });
    return postOf(typeof answer === "function" ? answer(body) : answer)(path, body);
  };
  return { calls, post };
}

test("the session's mode: the setting, an explicit off, and unknown", () => {
  assert.equal(sealingMode({ sealing: "on" }, undefined), "on");
  assert.equal(sealingMode({ sealing: "off" }, undefined), "off");
  // Without an answer, sealing keeps its default: off.
  assert.equal(sealingMode(undefined, undefined), "off");
  assert.equal(sealingMode("garbage", ""), "off");
  assert.equal(sealingMode(undefined, "maybe"), "off");
  // An explicit off always wins, so a broken helper can be switched off.
  for (const env of [undefined, "", "1", "maybe"]) assert.equal(envTurnsOff(env), false);
  for (const env of ["0", "false", "no", "OFF"]) {
    assert.equal(envTurnsOff(env), true);
    assert.equal(sealingMode({ sealing: "on" }, env), "off");
    assert.equal(sealingMode(undefined, env), "off");
  }
  // Without an answer, an explicit on does not make it known: the user is told it cannot run.
  for (const env of ["1", "true", "yes", " ON "]) {
    assert.equal(sealingMode(undefined, env), "unknown");
    assert.equal(sealingMode({ sealing: "off" }, env), "off");
  }
});

test("no replacement: the prompt passes unchanged", async () => {
  const { post } = recorder({ ok: true, status: 200, result: { revision: "r1", texts: [null] } });
  assert.deepEqual(await sealPrompt({ text: "fix the build" }, { post }), { kind: "pass" });
});

test("a registered secret is replaced without a note", async () => {
  const text = `use ${TOKEN} please`;
  const { post } = recorder(sealed(text, [[TOKEN, OLD_REF, false]]));
  assert.deepEqual(await sealPrompt({ text }, { post }), {
    kind: "rewrite",
    text: `use ${OLD_REF} please`,
    context: [],
  });
});

test("a temporary reference is replaced and the note names it once", async () => {
  // Offsets count code points: the emoji ahead of the secret is one, not two.
  const text = `🔑 ${TOKEN} and ${TOKEN}, plus sk-old-secret-value`;
  const n = TOKEN.length;
  const answer = {
    ok: true,
    status: 200,
    result: {
      revision: "r2",
      texts: [{
        text: `🔑 ${REF} and ${REF}, plus ${OLD_REF}`,
        replaced: [
          { start: 2, end: 2 + n, reference: REF, created: true, temporary: true },
          { start: 7 + n, end: 7 + 2 * n, reference: REF, created: false, temporary: true },
          { start: 14 + 2 * n, end: 33 + 2 * n, reference: OLD_REF, created: false, temporary: false },
        ],
      }],
    },
  };

  const decision = await sealPrompt({ text }, { post: recorder(answer).post });
  assert.deepEqual(decision, {
    kind: "rewrite",
    text: `🔑 ${REF} and ${REF}, plus ${OLD_REF}`,
    context: [sealNote([REF])],
  });
});

// The note's wording, pinned word for word.
const NOTE_TEMPLATE =
  "OpenViking stored a secret from the user's message and replaced it with a reference: <reference>[, <reference> …]. You will not see the value. Unless it is clearly for one-off use, save it under a proper name now with privacy_upsert (or `ov privacy upsert`), passing the reference as the value and a short description of what it is for, for example category=\"credential\", target_key=\"github\", values={\"token\": \"<reference>\"}, description=\"GitHub PAT for the acme org\". The temporary entry is removed once it is saved, so use the reference that privacy_upsert returns from then on. Write the reference as is wherever the secret is needed, for example `ov privacy run --env NAME='<reference>' -- sh -c '<command using \"$NAME\">'`; do not resolve it to plain text or print any part of it. Tell the user what was stored and under which name.";

test("the note is the pinned wording with the enrolled references filled in", () => {
  const expected = NOTE_TEMPLATE
    .replace("<reference>[, <reference> …]", `${REF}, ${OLD_REF}`)
    .replace('"token": "<reference>"', `"token": "${REF}"`);
  assert.equal(sealNote([REF, OLD_REF]), expected);
});

test("the note follows temporary, not created", async () => {
  const text = `use ${TOKEN}`;
  const make = (created, temporary) => {
    const answer = sealed(text, [[TOKEN, REF, temporary]]);
    answer.result.texts[0].replaced[0].created = created;
    return answer;
  };
  // Enrolled earlier (a count_tokens call, another replica): still owed.
  let decision = await sealPrompt({ text }, { post: recorder(make(false, true)).post });
  assert.deepEqual(decision.context, [sealNote([REF])]);
  // Enrolled by this call but already saved under a name: not owed.
  decision = await sealPrompt({ text }, { post: recorder(make(true, false)).post });
  assert.deepEqual(decision.context, []);
});

test("slash commands, meta prompts and empty prompts never reach the server", async () => {
  const post = async () => {
    throw new Error("must not run");
  };
  for (const prompt of [
    { text: `/review ${TOKEN}`, command: { name: "review", args: TOKEN } },
    { text: TOKEN, isMeta: true },
    { text: "" },
    { text: "   \n" },
  ]) {
    assert.deepEqual(await sealPrompt(prompt, { post }), { kind: "pass" });
    assert.equal(isSealable(prompt), false);
  }
  assert.equal(isSealable({ text: "hi" }), true);
});

test("a failed seal call stops the prompt, naming why and what to do", async () => {
  const decision = await sealPrompt({ text: `key ${TOKEN}` }, { post: postOf({ ok: false, status: 0, error: { message: "fetch failed" } }) });
  assert.equal(decision.kind, "drop");
  assert.match(decision.reason, /^OpenViking did not send this prompt: sealing secrets failed \(cannot reach the OpenViking server: fetch failed\)/);
  assert.match(decision.reason, /Try again once the OpenViking server answers, or turn sealing off with sealSecrets: false \(OPENVIKING_SEAL_SECRETS=0\)/);
});

// ── tools and attachments ───────────────────────────────────────────────

const VALUE = "ghp_REGISTEREDsecretVALUE1234567890abcd";
const SAVED = "{{ov_privacy:alice:viking://~/privacy/credential/github#/values/token}}";

// A server that knows VALUE as SAVED: seal replaces it, resolve gives it back.
function vault({ resolve = (selector) => (selector === SAVED ? { status: 200, value: VALUE } : { status: 404 }), seal } = {}) {
  const calls = [];
  const post = async (path, body) => {
    calls.push({ path, body });
    if (path.endsWith("/resolve")) {
      const answer = resolve(body.selector);
      if (answer instanceof Error) throw answer;
      return answer.status === 200
        ? { status: 200, json: { status: "ok", result: { value: answer.value } } }
        : { status: answer.status, json: { status: "error", error: { message: "no" } } };
    }
    if (seal) return seal(body);
    const texts = body.texts.map(({ text }) => {
      if (!text.includes(VALUE)) return null;
      const replaced = [];
      let from = 0;
      for (let at = text.indexOf(VALUE); at >= 0; at = text.indexOf(VALUE, from)) {
        const start = Array.from(text.slice(0, at)).length;
        replaced.push({ start, end: start + VALUE.length, reference: SAVED, created: false, temporary: false });
        from = at + VALUE.length;
      }
      return { text: text.split(VALUE).join(SAVED), replaced };
    });
    return { status: 200, json: { status: "ok", result: { revision: "r1", texts } } };
  };
  return { calls, post };
}

test("unsealCall: a reference in the arguments becomes the value, reserved keys stay", async () => {
  const { post, calls } = vault();
  const input = { tool: "Bash", tool_use_id: "t1", command: `curl -H "Authorization: ${SAVED}" x`, description: "fetch" };
  const { call, replaced } = await unsealCall(post, input);
  assert.equal(replaced, 1);
  assert.deepEqual(call, { ...input, command: `curl -H "Authorization: ${VALUE}" x` });
  assert.deepEqual(calls.map((c) => c.path), ["/api/v1/privacy-configs/resolve", "/api/v1/privacy-configs/seal"]);
  // The round-trip check never enrolls.
  assert.deepEqual(calls[1].body, { texts: [{ text: VALUE, enroll: false }] });
});

test("unsealCall: nothing to resolve makes no request and keeps the input", async () => {
  const { post, calls } = vault();
  const input = { tool: "Read", tool_use_id: "t1", file_path: "/tmp/x" };
  const res = await unsealCall(post, input);
  assert.equal(res.call, input);
  assert.equal(calls.length, 0);
});

test("unsealCall: OpenViking's and agent-spawning tools keep the references", async () => {
  const { post, calls } = vault();
  for (const tool of ["mcp__plugin_openviking-memory_openviking__privacy_upsert", ...AGENT_TOOLS]) {
    const input = { tool, prompt: `use ${SAVED}` };
    assert.equal((await unsealCall(post, input)).call, input);
  }
  assert.equal(calls.length, 0);
  assert.ok(AGENT_TOOLS.has("Agent"));
});

test("unsealCall: an `ov privacy` command and an unknown reference stay as written", async () => {
  const { post } = vault();
  const ovRun = { tool: "Bash", command: `ov privacy run --env T='${SAVED}' -- sh -c 'x "$T"'` };
  assert.equal((await unsealCall(post, ovRun)).call, ovRun);
  const missing = { tool: "Bash", command: `echo ${SAVED.replace("github", "gone")}` };
  assert.equal((await unsealCall(post, missing)).call, missing);
});

test("unsealCall: a failed resolve blocks the call", async () => {
  const { post } = vault({ resolve: () => new Error("ECONNREFUSED") });
  const res = await unsealCall(post, { tool: "Bash", command: `echo ${SAVED}` });
  assert.match(res.deny, /^OpenViking blocked this tool call: .*could not resolve a secret reference: cannot reach the OpenViking server: ECONNREFUSED/);
  assert.match(res.deny, /The tool did not run/);
  const res500 = await unsealCall(vault({ resolve: () => ({ status: 500 }) }).post, { tool: "Bash", command: `echo ${SAVED}` });
  assert.match(res500.deny, /HTTP 500/);
});

test("sealToolResult: the record and its context are sealed, an unchanged result is kept", async () => {
  const { post, calls } = vault();
  const outcome = { ref: 3, text: `out ${VALUE}`, result: { stdout: `token=${VALUE}\n`, stderr: "", interrupted: false } };
  assert.deepEqual(await sealToolResult(post, outcome), {
    result: { stdout: `token=${SAVED}\n`, stderr: "", interrupted: false },
  });
  assert.deepEqual(calls.at(-1).body, { texts: [{ text: `token=${VALUE}\n`, enroll: false }] });
  const withContext = { ...outcome, context: [`ctx ${VALUE}`] };
  assert.deepEqual((await sealToolResult(post, withContext, { enroll: true })).context, [`ctx ${SAVED}`]);
  assert.equal(calls.at(-1).body.texts.every((t) => t.enroll === true), true);
  const clean = { ref: 4, text: "fine", result: { stdout: "all fine here", stderr: "" } };
  assert.equal(await sealToolResult(post, clean), clean);
  const denied = { deny: "no" };
  assert.equal(await sealToolResult(post, denied), denied);
});

test("sealToolResult: an error's text is sealed and given back as a deny", async () => {
  const { post } = vault();
  const failed = { isError: true, ref: 5, text: `Exit code 1\nbad token ${VALUE}`, result: "x" };
  assert.deepEqual(await sealToolResult(post, failed), { deny: `Exit code 1\nbad token ${SAVED}` });
  const plain = { isError: true, text: "Exit code 1\nnothing secret" };
  assert.equal(await sealToolResult(post, plain), plain);
  // The context a hook attached stays, sealed, after the error's text.
  const noted = { isError: true, text: "Exit code 1", context: [`hint: ${VALUE}`] };
  assert.deepEqual(await sealToolResult(post, noted), { deny: `Exit code 1\n\nhint: ${SAVED}` });
});

test("sealToolResult: a failed seal withholds the result", async () => {
  const { post } = vault({ seal: async () => ({ status: 503, json: {} }) });
  const res = await sealToolResult(post, { result: { stdout: `token=${VALUE}` } });
  assert.deepEqual(res, { deny: withheld("HTTP 503") });
  assert.doesNotMatch(res.deny, new RegExp(VALUE));
});

test("sealAttachment: sealed without enroll, withheld on failure", async () => {
  const { post, calls } = vault();
  assert.equal(await sealAttachment(post, `file: ${VALUE}`), `file: ${SAVED}`);
  assert.deepEqual(calls[0].body, { texts: [{ text: `file: ${VALUE}`, enroll: false }] });
  assert.equal(await sealAttachment(post, "plain text"), "plain text");
  const broken = vault({ seal: async () => ({ status: 500, json: {} }) });
  const text = await sealAttachment(broken.post, `file: ${VALUE}`);
  assert.match(text, /^\[OpenViking withheld this attached content: sealing secrets in it failed \(HTTP 500\)/);
});

test("serverPost: URL, headers and body, the parsed answer, and a timeout", async () => {
  const seen = [];
  const fetch = async (url, init) => {
    seen.push({ url, init });
    return { status: 200, text: '{"status":"ok","result":{"value":"v"}}' };
  };
  const post = serverPost(fetch, { baseUrl: "http://ov:1933", headers: { Authorization: "Bearer k" } });
  assert.deepEqual(await post("/api/v1/privacy-configs/resolve", { selector: "s" }), {
    status: 200,
    json: { status: "ok", result: { value: "v" } },
  });
  assert.deepEqual(seen, [{
    url: "http://ov:1933/api/v1/privacy-configs/resolve",
    init: { method: "POST", headers: { Authorization: "Bearer k", "Content-Type": "application/json" }, body: '{"selector":"s"}' },
  }]);
  const html = serverPost(async () => ({ status: 200, text: "<html>" }), { baseUrl: "http://ov" });
  assert.deepEqual(await html("/x", {}), { status: 200, json: null });
  const slow = serverPost(() => new Promise(() => {}), { baseUrl: "http://ov" }, { timeoutMs: 20 });
  await assert.rejects(slow("/x", {}), { name: "TimeoutError" });
  await assert.rejects(serverPost(fetch, undefined)("/x", {}), /no OpenViking server URL/);
  // Through the core, a timeout reads as one.
  const decision = await sealPrompt({ text: "hello there" }, { post: slow });
  assert.match(decision.reason, /did not answer in time/);
});

// ── a server that cannot seal ───────────────────────────────────────────

for (const [name, check, pattern, hint] of [
  ["a server too old by the table", () => ({ supported: false, required: "0.5.7", actual: "0.5.6" }),
    /server is version 0\.5\.6, and sealing secrets needs 0\.5\.7 or newer/, /Upgrade the OpenViking server/],
  ["a failing health check", () => ({ supported: false, required: "0.5.7", actual: null }),
    /version could not be read/, /running and reachable/],
  ["an unknown setting", () => settingUnknown(),
    /OPENVIKING_SEAL_SECRETS is on, but the plugin's settings could not be read here/, /Use Claude Code where the plugin can run its setting helper/],
]) {
  test(`${name} stops at each point with the reason named, without a call`, async () => {
    const server = vault();
    const post = createSealPolicy({ check }).guard(server.post);
    const drop = await sealPrompt({ text: `key ${VALUE}` }, { post });
    assert.equal(drop.kind, "drop");
    assert.match(drop.reason, pattern);
    assert.match(drop.reason, hint);
    assert.match((await unsealCall(post, { tool: "Bash", command: `echo ${SAVED}` })).deny, pattern);
    assert.match((await sealToolResult(post, { result: { stdout: `token=${VALUE}` } })).deny, /withheld/);
    assert.match(await sealAttachment(post, `file: ${VALUE}`), /withheld/);
    assert.equal(server.calls.length, 0);
  });
}
