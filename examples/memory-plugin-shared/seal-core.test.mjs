import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

import {
  BATCH_BYTES,
  MAX_TEXTS,
  RESOLVE_PATH,
  SEAL_PATH,
  SESSION_TEXT,
  SealError,
  containsOvPrivacy,
  createSealPolicy,
  stopHint,
  whyNotSealed,
  isCanonicalReference,
  isOpenVikingTool,
  sealNote,
  sealTexts,
  sealValue,
  unsealValue,
  withheld,
} from "../agent-hook-plugin/lib/seal-core.mjs";

const ref = (target, category = "credential", owner = "alice") =>
  `{{ov_privacy:${owner}:viking://~/privacy/${category}/${target}#/values/token}}`;
const GH = "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const GH_REF = ref("github");
const NEW = "sk-new-secret-1234567890";
const NEW_REF = ref("openai-k3j2", "auto");

/**
 * A fake server: `vault` maps value -> reference (auto references are
 * temporary); `enrollable` values join the vault when sent with enroll.
 * Records every call. `override(path, body, calls)` may return an answer.
 */
function fakeServer({ vault = {}, enrollable = {}, override } = {}) {
  const calls = [];
  const known = new Map(Object.entries(vault));
  const post = async (path, body) => {
    calls.push({ path, body: structuredClone(body) });
    const forced = override?.(path, body, calls);
    if (forced !== undefined) return typeof forced === "function" ? forced() : forced;
    if (path === RESOLVE_PATH) {
      for (const [value, reference] of known) {
        if (reference === body.selector) return { status: 200, json: { status: "ok", result: { value } } };
      }
      return { status: 404, json: { status: "error", error: { message: "not found" } } };
    }
    assert.equal(path, SEAL_PATH);
    const texts = body.texts.map(({ text, enroll }) => {
      if (enroll) {
        for (const [value, reference] of Object.entries(enrollable)) {
          if (text.includes(value) && !known.has(value)) known.set(value, reference);
        }
      }
      return sealOne(text, known);
    });
    return { status: 200, json: { status: "ok", result: { revision: "r1", texts } } };
  };
  return { post, calls, known };
}

function sealOne(text, known) {
  const hits = [];
  for (const [value, reference] of known) {
    let at = text.indexOf(value);
    while (at >= 0) {
      hits.push({ at, value, reference });
      at = text.indexOf(value, at + value.length);
    }
  }
  if (!hits.length) return null;
  hits.sort((a, b) => a.at - b.at);
  let out = "";
  let cursor = 0;
  const replaced = [];
  for (const { at, value, reference } of hits) {
    const start = Array.from(text.slice(0, at)).length;
    replaced.push({
      start,
      end: start + Array.from(value).length,
      reference,
      created: false,
      temporary: reference.includes("/privacy/auto/"),
    });
    out += text.slice(cursor, at) + reference;
    cursor = at + value.length;
  }
  return { text: out + text.slice(cursor), replaced };
}

const answer = (texts) => ({ status: 200, json: { status: "ok", result: { revision: "r1", texts } } });

async function sealError(promise, pattern) {
  await assert.rejects(promise, (error) => {
    assert.ok(error instanceof SealError, `expected SealError, got ${error}`);
    if (pattern) assert.match(error.message, pattern);
    return true;
  });
}

// ── sealTexts ───────────────────────────────────────────────────────────

test("sealTexts returns each text to use with its temporary references", async () => {
  const { post, calls } = fakeServer({ vault: { [GH]: GH_REF }, enrollable: { [NEW]: NEW_REF } });
  const result = await sealTexts(post, [
    { text: `🔑 ${GH} and ${NEW} and ${NEW}`, enroll: true },
    { text: "nothing here" },
    { text: `again ${GH}` },
  ]);
  assert.deepEqual(calls[0].body, {
    texts: [
      { text: `🔑 ${GH} and ${NEW} and ${NEW}`, enroll: true },
      { text: "nothing here", enroll: false },
      { text: `again ${GH}`, enroll: false },
    ],
  });
  assert.deepEqual(result.texts.map((t) => [t.text, t.changed, t.temporary]), [
    [`🔑 ${GH_REF} and ${NEW_REF} and ${NEW_REF}`, true, [NEW_REF]],
    ["nothing here", false, []],
    [`again ${GH_REF}`, true, []],
  ]);
  assert.deepEqual(result.temporary, [NEW_REF]);
  // Code points: the emoji is one.
  assert.deepEqual(result.texts[0].spans[0], { start: 2, end: 2 + GH.length, reference: GH_REF, temporary: false });
});

test("sealTexts sends no request for no texts, and a duplicate once", async () => {
  const { post, calls } = fakeServer();
  assert.deepEqual(await sealTexts(post, []), { texts: [], temporary: [] });
  assert.equal(calls.length, 0);
  const result = await sealTexts(post, [{ text: "same text" }, { text: "same text" }, { text: "same text", enroll: true }]);
  assert.equal(calls[0].body.texts.length, 2);
  assert.equal(result.texts.length, 3);
});

test("sealTexts splits at 1024 texts and at the byte budget", async () => {
  const { post, calls } = fakeServer({ vault: { [GH]: GH_REF } });
  const items = Array.from({ length: MAX_TEXTS * 2 + 5 }, (_, i) => ({ text: `text ${i} ${GH}` }));
  const result = await sealTexts(post, items);
  assert.deepEqual(calls.map((c) => c.body.texts.length), [MAX_TEXTS, MAX_TEXTS, 5]);
  assert.equal(result.texts[2050].text, `text 2050 ${GH_REF}`);

  calls.length = 0;
  const big = "x".repeat(Math.floor(BATCH_BYTES / 2.5));
  await sealTexts(post, [{ text: `a${big}` }, { text: `b${big}` }, { text: `c${big}` }, { text: "small one" }]);
  assert.deepEqual(calls.map((c) => c.body.texts.length), [2, 2]);
  // A text larger than the budget goes alone.
  calls.length = 0;
  const huge = "y".repeat(BATCH_BYTES + 10);
  await sealTexts(post, [{ text: "before it" }, { text: huge }, { text: "after it" }]);
  assert.deepEqual(calls.map((c) => c.body.texts.map((t) => t.text.length)), [[9], [huge.length], [8]]);
  await sealError(sealTexts(post, [{ text: "z".repeat(16 * 1024 * 1024 + 1) }]), /larger than/);
});

test("transport failures, timeouts and non-ok answers are a SealError", async () => {
  const cases = [
    [async () => { throw new Error("connect ECONNREFUSED"); }, /cannot reach the OpenViking server: connect ECONNREFUSED/],
    [async () => { throw Object.assign(new Error("x"), { name: "AbortError" }); }, /did not answer in time/],
    [async () => { throw Object.assign(new Error("x"), { name: "TimeoutError" }); }, /did not answer in time/],
    [async () => { throw Object.assign(new Error("x"), { aborted: true }); }, /did not answer in time/],
    [async () => ({ status: 404, json: { detail: "Not Found" } }), /HTTP 404: .*too old/],
    [async () => ({ status: 413, json: { status: "error", error: { message: "too large" } } }), /HTTP 413: too large/],
    [async () => ({ status: 500, json: null }), /^HTTP 500$/],
    [async () => ({ status: 200, json: { status: "error", result: {} } }), /not ok/],
    [async () => ({ status: 200, json: "nonsense" }), /not ok/],
    [async () => null, /no answer/],
    [async () => ({ json: {} }), /no HTTP status/],
  ];
  for (const [post, pattern] of cases) await sealError(sealTexts(post, [{ text: `key ${GH}` }]), pattern);
  await sealError(sealTexts(async () => answer([null]), [{ text: 7 }]), /string text/);
  // A status on HTTP failures, for adapters that care.
  await assert.rejects(sealTexts(async () => ({ status: 503, json: {} }), [{ text: "x" }]), { status: 503 });
});

// One good answer for `key GH`, with its item or first span changed.
const TEXT = `key ${GH}`;
const GOOD_ITEM = sealOne(TEXT, new Map([[GH, GH_REF]]));
function withSpan(change) {
  const span = { ...GOOD_ITEM.replaced[0], ...change };
  for (const [key, value] of Object.entries(change)) if (value === undefined) delete span[key];
  const text = typeof span.reference === "string" ? GOOD_ITEM.text.replace(GH_REF, span.reference) : GOOD_ITEM.text;
  return answer([{ text, replaced: [span] }]);
}

test("a span needs offsets in order and in range and a canonical reference", async () => {
  assert.equal((await sealTexts(async () => answer([GOOD_ITEM]), [{ text: TEXT }])).texts[0].text, GOOD_ITEM.text);
  const result = (texts) => ({ status: 200, json: { status: "ok", result: { revision: "r1", texts } } });
  const cases = [
    [{ status: 200, json: { status: "ok" } }, /no result/],
    [result([]), /does not match the request/],
    [result([null, null]), /does not match the request/],
    [result("x"), /does not match the request/],
    [answer(["x"]), /not an object or null/],
    [answer([[]]), /not an object or null/],
    [answer([{ ...GOOD_ITEM, replaced: "x" }]), /replaced is not a list/],
    [answer([{ ...GOOD_ITEM, replaced: [null] }]), /not an object/],
    [withSpan({ end: 999 }), /out of order or out of range/],
    [withSpan({ start: 5, end: 5 }), /out of order or out of range/],
    [withSpan({ start: 1.5 }), /out of order or out of range/],
    [withSpan({ start: -1 }), /out of order or out of range/],
  ];
  // Two spans out of order.
  const twice = `${GH} ${GH}`;
  const two = sealOne(twice, new Map([[GH, GH_REF]]));
  cases.push([answer([{ ...two, replaced: [two.replaced[1], two.replaced[0]] }]), /out of order/]);
  for (const reference of [
    "[redacted]",
    "{{ov_privacy:x}}",
    ref("github", "contact"),
    ref("github", "credentials"),
    ref("github", "autox"),
    "{{ov_privacy:alice:viking://~/privacy/auto#/values/token}}",
    "{{ov_privacy:alice:viking://user/alice/privacy/credential/github#/values/token}}",
    "{{ov_privacy:alice:viking://~/privacy/credential/github}}",
    "{{ov_privacy:alice:viking://~/privacy/credential/#/values/token}}",
    "{{ov_privacy:alice:viking://~/privacy/credential/github#/values/}}",
    "{{ov_privacy:al ice:viking://~/privacy/credential/github#/values/token}}",
    "{{ov_privacy:alice:viking://~/privacy/credential/a/b#/values/token}}",
    "{{ ov_privacy:alice:viking://~/privacy/credential/github#/values/token }}",
    `${GH_REF}}}`,
  ]) {
    cases.push([withSpan({ reference }), /no canonical reference/]);
  }
  for (const [reply, pattern] of cases) {
    await sealError(sealTexts(async () => reply, [{ text: TEXT }]), pattern);
  }
  // The text is built from the spans, and no spans is no change.
  const built = await sealTexts(async () => answer([{ ...GOOD_ITEM, text: 7 }]), [{ text: TEXT }]);
  assert.equal(built.texts[0].text, GOOD_ITEM.text);
  const none = await sealTexts(async () => answer([{ ...GOOD_ITEM, replaced: [] }]), [{ text: TEXT }]);
  assert.equal(none.texts[0].changed, false);
});

test("the canonical reference pattern takes credential and auto only", () => {
  assert.equal(isCanonicalReference(GH_REF), true);
  assert.equal(isCanonicalReference(NEW_REF), true);
  assert.equal(isCanonicalReference(` ${GH_REF}`), false);
  assert.equal(isCanonicalReference(ref("x", "contact")), false);
  assert.equal(isCanonicalReference(null), false);
});

// ── sealValue ───────────────────────────────────────────────────────────

test("sealValue seals every string leaf in one call, not keys or short strings", async () => {
  const { post, calls } = fakeServer({ vault: { [GH]: GH_REF }, enrollable: { [NEW]: NEW_REF } });
  const untouched = { deep: ["plain text", 42, null, true] };
  const value = {
    [GH]: "key is a secret",
    content: [{ type: "text", text: `out ${GH}` }, { type: "text", text: `new ${NEW}` }],
    untouched,
    short: "ghp_1",
    again: `out ${GH}`,
    nested: { list: [[`x ${NEW}`]] },
  };
  const before = structuredClone(value);
  const result = await sealValue(post, value, { enroll: true });
  assert.equal(calls.length, 1);
  // Distinct leaves of 6 code points or more, all enrollable.
  assert.deepEqual(
    calls[0].body.texts,
    ["key is a secret", `out ${GH}`, `new ${NEW}`, "plain text", `x ${NEW}`].map((text) => ({ text, enroll: true })),
  );
  assert.deepEqual(result.value, {
    [GH]: "key is a secret",
    content: [{ type: "text", text: `out ${GH_REF}` }, { type: "text", text: `new ${NEW_REF}` }],
    untouched,
    short: "ghp_1",
    again: `out ${GH_REF}`,
    nested: { list: [[`x ${NEW_REF}`]] },
  });
  assert.equal(result.sealed, 4);
  assert.deepEqual(result.temporary, [NEW_REF]);
  // The input is not modified; unchanged parts keep their identity.
  assert.deepEqual(value, before);
  assert.equal(result.value.untouched, untouched);
});

test("sealValue: nothing to seal returns the input itself; enroll defaults to false", async () => {
  const { post, calls } = fakeServer({ enrollable: { [NEW]: NEW_REF } });
  const value = { a: ["short", 1], b: `text with ${NEW}` };
  const result = await sealValue(post, value);
  assert.equal(result.value, value);
  assert.deepEqual(result.temporary, []);
  assert.equal(result.sealed, 0);
  assert.equal(calls[0].body.texts[0].enroll, false);

  calls.length = 0;
  const bare = await sealValue(post, "tiny");
  assert.equal(bare.value, "tiny");
  assert.equal(calls.length, 0);
  assert.deepEqual((await sealValue(post, `${NEW}`, { enroll: true })).value, NEW_REF);
});

test("sealValue leaves data: URIs and long base64 runs out", async () => {
  const { post, calls } = fakeServer({ vault: { [GH]: GH_REF } });
  const image = "iVBORw0KGgo" + "A".repeat(300) + "+/=";
  const uri = `data:image/png;base64,${image}${GH}`;
  const almost = "A".repeat(255);
  const spaced = `${"A".repeat(300)} ${GH}`;
  const value = { data: image, uri, almost, spaced, plain: `out ${GH}` };
  const result = await sealValue(post, value);
  assert.deepEqual(
    calls[0].body.texts.map(({ text }) => text),
    [almost, spaced, `out ${GH}`],
  );
  assert.equal(result.value.data, image);
  assert.equal(result.value.uri, uri);
  assert.equal(result.value.spaced, `${"A".repeat(300)} ${GH_REF}`);

  calls.length = 0;
  assert.equal((await sealValue(post, { data: image, uri })).sealed, 0);
  assert.equal(calls.length, 0);

  // Image blocks of both shapes keep their data; file_data too.
  const blocks = [
    { type: "image", source: { type: "base64", media_type: "image/png", data: image } },
    { type: "image", data: image, mimeType: "image/png" },
    { type: "file", file: { file_data: image } },
  ];
  assert.equal((await sealValue(post, blocks)).value, blocks);
  assert.ok(calls.every(({ body }) => body.texts.every(({ text }) => text !== image)));
});

test("sealValue seals a long base64 run that is not under data or file_data", async () => {
  const token = "FwoGZXIvYXdzE" + "B".repeat(300);
  const { post, calls } = fakeServer({ vault: { [token]: GH_REF } });
  const value = { content: [{ type: "text", text: token }], data: token, structured: { token } };
  const result = await sealValue(post, value);
  assert.deepEqual(calls[0].body.texts, [{ text: token, enroll: false }]);
  assert.deepEqual(result.value, { content: [{ type: "text", text: GH_REF }], data: token, structured: { token: GH_REF } });
  assert.equal(result.sealed, 2);
  assert.equal((await sealValue(post, token)).value, GH_REF);
});

test("sealValue fails as a whole", async () => {
  await sealError(sealValue(async () => ({ status: 500, json: {} }), { a: `x ${GH}` }), /HTTP 500/);
});

// ── unsealValue ─────────────────────────────────────────────────────────

test("unsealValue puts values in place of whole references and reuses each resolution", async () => {
  const { post, calls } = fakeServer({ vault: { [GH]: GH_REF, [NEW]: NEW_REF } });
  const keep = { list: ["no reference here"] };
  const value = {
    command: `curl -H "Authorization: token ${GH_REF}" && echo ${GH_REF}`,
    env: { KEY: NEW_REF },
    keep,
    [GH_REF]: "keys are left alone",
  };
  const result = await unsealValue(post, value);
  assert.deepEqual(result.value, {
    command: `curl -H "Authorization: token ${GH}" && echo ${GH}`,
    env: { KEY: NEW },
    keep,
    [GH_REF]: "keys are left alone",
  });
  assert.equal(result.replaced, 3);
  assert.equal(result.value.keep, keep);
  // One resolve per reference, then one seal call for the round trip.
  assert.deepEqual(calls.map((c) => c.path), [RESOLVE_PATH, RESOLVE_PATH, SEAL_PATH]);
  assert.deepEqual(calls.slice(0, 2).map((c) => c.body), [{ selector: GH_REF }, { selector: NEW_REF }]);
  assert.deepEqual(calls[2].body, { texts: [{ text: GH, enroll: false }, { text: NEW, enroll: false }] });
  assert.match(value.command, /ov_privacy/, "the input is not modified");
});

test("unsealValue: no reference means no request", async () => {
  const { post, calls } = fakeServer();
  const value = { a: "plain", b: ["{{ov_privacy:not-a-reference}}"] };
  assert.deepEqual(await unsealValue(post, value), { value, replaced: 0 });
  assert.equal(calls.length, 0);
});

test("unsealValue: a 404 leaves the reference as it is", async () => {
  const { post } = fakeServer({ vault: { [GH]: GH_REF } });
  const missing = ref("gone");
  const result = await unsealValue(post, { a: `${missing} ${GH_REF}` });
  assert.deepEqual(result, { value: { a: `${missing} ${GH}` }, replaced: 1 });
});

test("unsealValue: a value that does not seal back to exactly its reference is left", async () => {
  const other = ref("other");
  const cases = [
    // Seals to another reference.
    (text) => ({ text: other, replaced: [{ start: 0, end: Array.from(text).length, reference: other, created: false, temporary: false }] }),
    // Not sealed at all (never_mask, too short).
    () => null,
    // One span, but not over the whole value.
    (text) => ({ text: `${text.slice(0, 2)}${GH_REF}`, replaced: [{ start: 2, end: Array.from(text).length, reference: GH_REF, created: false, temporary: false }] }),
  ];
  for (const item of cases) {
    const { post } = fakeServer({
      vault: { [GH]: GH_REF },
      override: (path, body) => (path === SEAL_PATH ? answer(body.texts.map(({ text }) => item(text))) : undefined),
    });
    const value = { a: `use ${GH_REF}` };
    assert.deepEqual(await unsealValue(post, value), { value, replaced: 0 });
  }
});

test("unsealValue: a string with an `ov privacy` command is left untouched as a whole", async () => {
  const { post, calls } = fakeServer({ vault: { [GH]: GH_REF } });
  const value = {
    command: `ov privacy run --env T='${GH_REF}' -- sh -c 'gh auth status'`,
    other: `echo ${GH_REF}`,
  };
  const result = await unsealValue(post, value);
  assert.equal(result.value.command, value.command);
  assert.equal(result.value.other, `echo ${GH}`);
  assert.equal(result.replaced, 1);
  assert.equal(calls.filter((c) => c.path === RESOLVE_PATH).length, 1);
  // Only the command itself: not a word that merely ends in "ov".
  for (const text of ["ov privacy upsert", "x; ov  privacy tree", "/usr/bin/ov privacy run"]) {
    assert.equal(containsOvPrivacy(text), true, text);
  }
  for (const text of ["gov privacy", "my-ov privacy", "ovprivacy", "ov privacyx", "ov private"]) {
    assert.equal(containsOvPrivacy(text), false, text);
  }
});

test("unsealValue: skip returns the arguments as written without a request", async () => {
  const { post, calls } = fakeServer({ vault: { [GH]: GH_REF } });
  const value = { prompt: `use ${GH_REF}` };
  assert.deepEqual(await unsealValue(post, value, { skip: true }), { value, replaced: 0 });
  assert.equal(calls.length, 0);
});

test("unsealValue: a 403 (another user's reference) leaves it as it is", async () => {
  const bob = ref("github", "credential", "bob");
  const { post } = fakeServer({
    vault: { [GH]: GH_REF },
    override: (path, body) =>
      path === RESOLVE_PATH && body.selector === bob
        ? { status: 403, json: { status: "error", error: { message: "belongs to user bob" } } }
        : undefined,
  });
  assert.deepEqual(await unsealValue(post, { a: `${bob} ${GH_REF}` }), { value: { a: `${bob} ${GH}` }, replaced: 1 });
});

test("unsealValue: transport and server errors are a failure", async () => {
  const cases = [
    [() => { throw new Error("ECONNRESET"); }, RESOLVE_PATH, /could not resolve.*cannot reach the OpenViking server: ECONNRESET/],
    [() => ({ status: 500, json: {} }), RESOLVE_PATH, /could not resolve a secret reference: HTTP 500/],
    [() => ({ status: 200, json: { status: "error" } }), RESOLVE_PATH, /could not resolve/],
    [() => ({ status: 500, json: {} }), SEAL_PATH, /could not confirm a secret reference: HTTP 500/],
    [() => answer([]), SEAL_PATH, /could not confirm.*does not match the request/],
  ];
  for (const [reply, at, pattern] of cases) {
    const { post } = fakeServer({ vault: { [GH]: GH_REF }, override: (path) => (path === at ? reply : undefined) });
    await sealError(unsealValue(post, { a: GH_REF }), pattern);
  }
  // A resolved value that is empty or not a string leaves the reference.
  for (const result of [{ value: "" }, { value: 7 }, {}]) {
    const { post } = fakeServer({ override: (path) => (path === RESOLVE_PATH ? { status: 200, json: { status: "ok", result } } : undefined) });
    assert.deepEqual(await unsealValue(post, { a: GH_REF }), { value: { a: GH_REF }, replaced: 0 });
  }
});

test("seal and unseal round-trip", async () => {
  const { post } = fakeServer({ vault: { [GH]: GH_REF } });
  const sealed = await sealValue(post, { output: `token=${GH}` });
  assert.deepEqual(sealed.value, { output: `token=${GH_REF}` });
  const unsealed = await unsealValue(post, sealed.value);
  assert.deepEqual(unsealed.value, { output: `token=${GH}` });
});

// ── texts and names ─────────────────────────────────────────────────────

// The note's wording, pinned word for word.
const NOTE_TEMPLATE =
  "OpenViking stored a secret from the user's message and replaced it with a reference: <reference>[, <reference> …]. You will not see the value. Unless it is clearly for one-off use, save it under a proper name now with privacy_upsert (or `ov privacy upsert`), passing the reference as the value and a short description of what it is for, for example category=\"credential\", target_key=\"github\", values={\"token\": \"<reference>\"}, description=\"GitHub PAT for the acme org\". The temporary entry is removed once it is saved, so use the reference that privacy_upsert returns from then on. Write the reference as is wherever the secret is needed, for example `ov privacy run --env NAME='<reference>' -- sh -c '<command using \"$NAME\">'`; do not resolve it to plain text or print any part of it. Tell the user what was stored and under which name.";

test("the note is the pinned wording with the references filled in", () => {
  const second = ref("aws-x1", "auto");
  assert.equal(
    sealNote([NEW_REF, second]),
    NOTE_TEMPLATE
      .replace("<reference>[, <reference> …]", `${NEW_REF}, ${second}`)
      .replace('"token": "<reference>"', `"token": "${NEW_REF}"`),
  );
  // The run example keeps the literal placeholders.
  assert.match(sealNote([NEW_REF]), /--env NAME='<reference>' -- sh -c '<command using "\$NAME">'/);
});

test("the withheld text says why and shows nothing of the result", () => {
  const text = withheld("HTTP 503");
  assert.match(text, /^\[OpenViking withheld this tool result: sealing secrets in it failed \(HTTP 503\)\./);
  assert.match(text, /no secret reaches the model/);
  assert.doesNotMatch(withheld(), /\(\)/);
});

test("OpenViking's own tools are recognised by name", () => {
  for (const name of ["mcp__plugin_openviking-memory_openviking__read", "OpenViking_search", "openviking"]) {
    assert.equal(isOpenVikingTool(name), true, name);
  }
  for (const name of ["Bash", "Agent", "mcp__github__get", undefined, null, 3]) {
    assert.equal(isOpenVikingTool(name), false, String(name));
  }
  assert.equal(isOpenVikingTool("memory_recall", ["memory_recall"]), true);
  assert.equal(isOpenVikingTool("memory_recall"), false);
});

test("the session text is the gateway's SESSION_NOTE without its leading dash", (t) => {
  const source = new URL("../../openviking_gateway/sealing.py", import.meta.url);
  if (!existsSync(source)) return t.skip("the gateway source is not next to the examples");
  const block = readFileSync(source, "utf8").match(/^SESSION_NOTE = \(\n([\s\S]*?)\n\)/m);
  assert.ok(block, "SESSION_NOTE not found");
  const note = [...block[1].matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => JSON.parse(`"${m[1]}"`)).join("");
  assert.ok(note.startsWith("- "));
  assert.equal(SESSION_TEXT, note.slice(2));
});

// ── when sealing cannot be done ─────────────────────────────────────────

test("the policy makes no call while the server cannot seal, and keeps only a yes", async () => {
  let asked = 0;
  let actual = "0.5.6";
  const server = fakeServer({ vault: { [GH]: GH_REF } });
  const policy = createSealPolicy({
    check: () => {
      asked += 1;
      return { supported: actual !== "0.5.6", required: "0.5.7", actual };
    },
  });
  const send = policy.guard(server.post);
  await sealError(sealTexts(send, [{ text: `token ${GH}` }]), /version 0\.5\.6, and sealing secrets needs 0\.5\.7 or newer/);
  await assert.rejects(unsealValue(send, { command: `echo ${GH_REF}` }), (error) => error.reason === "server_too_old");
  assert.equal(asked, 2);
  assert.deepEqual(server.calls, []);
  // The server was upgraded: asked once more, then not again.
  actual = "0.5.7";
  assert.equal((await sealTexts(send, [{ text: `token ${GH}` }])).texts[0].text, `token ${GH_REF}`);
  await sealTexts(send, [{ text: `again ${GH}` }]);
  assert.equal(asked, 3);
  // Nothing to send: not even the support is needed.
  const fresh = createSealPolicy({ check: () => { throw new Error("must not be asked"); } });
  assert.deepEqual(await sealValue(fresh.guard(server.post), { n: 1, s: "abc" }), { value: { n: 1, s: "abc" }, temporary: [], sealed: 0 });
});

test("the policy: an unreadable version, a check that throws, a supported server", async () => {
  const unknown = await createSealPolicy({ check: () => ({ supported: false, required: "0.5.7", actual: null }) }).unavailable();
  assert.equal(unknown.reason, "version_unknown");
  assert.match(unknown.message, /version could not be read .* needs 0\.5\.7 or newer/);
  assert.equal((await createSealPolicy({ check: () => { throw new Error("x"); } }).unavailable()).reason, "version_unknown");
  const own = new SealError("the setting could not be read");
  assert.equal(await createSealPolicy({ check: () => own }).unavailable(), own);
  const server = fakeServer({ vault: { [GH]: GH_REF } });
  const ok = createSealPolicy({ check: async () => ({ supported: true, required: "0.5.7", actual: "0.5.7.dev44" }) });
  assert.equal(await ok.unavailable(), null);
  const { texts } = await sealTexts(ok.guard(server.post), [{ text: `token ${GH}` }]);
  assert.equal(texts[0].text, `token ${GH_REF}`);
});

test("the stop hint says what to do for each reason", () => {
  assert.equal(whyNotSealed("the seal hook timed out"), "sealing secrets failed (the seal hook timed out)");
  assert.match(stopHint(new SealError("x", { reason: "server_too_old" })), /^With sealSecrets on, OpenViking sends nothing unsealed\. Upgrade the OpenViking server, or turn sealing off with sealSecrets: false \(OPENVIKING_SEAL_SECRETS=0\)\.$/);
  assert.match(stopHint(new SealError("x", { reason: "version_unknown" })), /running and reachable/);
  assert.match(stopHint(new SealError("x", { reason: "setting_unknown" })), /Use Claude Code where the plugin can run its setting helper/);
  assert.match(stopHint(new SealError("HTTP 500: boom", { status: 500 })), /Try again once the OpenViking server answers/);
});

test("unsealValue keeps the reason of a server that cannot seal", async () => {
  const server = fakeServer({ vault: { [GH]: GH_REF } });
  const policy = createSealPolicy({ check: () => ({ supported: false, required: "0.5.7", actual: null }) });
  await assert.rejects(unsealValue(policy.guard(server.post), { command: `echo ${GH_REF}` }), (error) => {
    assert.equal(error.reason, "version_unknown");
    return true;
  });
  const failing = fakeServer({ override: () => ({ status: 500, json: { status: "error", error: { message: "x" } } }) });
  await assert.rejects(unsealValue(failing.post, { command: `echo ${GH_REF}` }), (error) => error.reason === "seal_failed");
});
