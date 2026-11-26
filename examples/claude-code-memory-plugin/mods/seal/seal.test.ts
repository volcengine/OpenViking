import type { On } from "claude-code";
import { describe, expect, mock, test } from "claude-code/testing";
import { SESSION_TEXT, sealNote } from "../../scripts/lib/prompt-seal.mjs";

// The wiring only: what reaches the session for each helper answer. The
// decisions themselves are tested with node --test in scripts/lib/prompt-seal.test.mjs.
const TOKEN = "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const REF = "{{ov_privacy:alice:viking://~/privacy/auto/github-k3j2m8#/values/token}}";
const TEXT = `use ${TOKEN}`;
// Typed at the prompt, as the engine raises it.
const TYPED = { text: TEXT, wait: false, origin: { kind: "composer" } } as const;

type Run = { exitCode: number; stdout: string } | Error;
type Answer = { status: number; json: unknown } | Error;

const SERVER = { baseUrl: "http://ov.test:1933", headers: { Authorization: "Bearer sk-test" } };

// The setting helper (`setting` answers each run) and the server, reached
// through $.http.fetch (`seal` and `resolve` answer each call).
function helper(
  on: On,
  { setting: read, seal, resolve, env = {} }: { setting: Run | (() => Run); seal?: Answer | ((body: any) => Answer); resolve?: Answer; env?: Record<string, string> },
) {
  mock.env(on, env);
  on("process.hookEnv", () => ({ value: { CLAUDE_PLUGIN_ROOT: "/plugin" } }));
  const runs: string[][] = [];
  const calls: { url: string; body: any; auth?: string }[] = [];
  on("process.run", (_$, e) => {
    runs.push([...e.argv]);
    if (!String(e.argv[1]).endsWith("/scripts/seal-prompt.mjs")) throw new Error("unexpected process run");
    const setting = typeof read === "function" ? read() : read;
    if (setting instanceof Error) throw setting;
    const stderr = setting.exitCode ? "boom" : "";
    return { value: { ...setting, stderr, isStdoutTruncated: false, isStderrTruncated: false } };
  });
  on("http.fetch", (_$, e) => {
    const body = JSON.parse(String(e.init?.body ?? "{}"));
    calls.push({ url: e.url, body, auth: e.init?.headers?.Authorization });
    const pick = e.url.endsWith("/resolve") ? resolve : seal;
    const answer = typeof pick === "function" ? pick(body) : pick;
    if (!answer) throw new Error("unexpected server call");
    if (answer instanceof Error) throw answer;
    return { value: { status: answer.status, ok: answer.status < 300, headers: {}, text: JSON.stringify(answer.json) } };
  });
  return { runs, calls };
}

const answer = (value: unknown) => ({ exitCode: 0, stdout: JSON.stringify(value) });
const support = (actual: string | null) => ({
  feature: "privacy_seal",
  supported: actual !== null && actual >= "0.5.7",
  required: "0.5.7",
  actual,
});
const ON = answer({ sealing: "on", enrollToolSecrets: false, server: SERVER, support: support("0.5.7") });
const SETTING_RUN = ["node", expect.stringMatching(/\/scripts\/seal-prompt\.mjs$/)];
const SEAL_URL = "http://ov.test:1933/api/v1/privacy-configs/seal";

function sessionSees(on: On) {
  const seen: { text: string; context?: readonly string[] }[] = [];
  on("prompt.submit", (_$, e) => {
    seen.push({ text: e.text, context: e.context });
    return { text: e.text, context: e.context };
  });
  return seen;
}

function logged(on: On) {
  const lines: string[] = [];
  on("ui.log", (_$, e) => {
    lines.push(e.text);
    return { value: undefined };
  });
  return lines;
}

const sealedAnswer = (temporary: boolean): Answer => ({
  status: 200,
  json: {
    status: "ok",
    result: {
      revision: "r1",
      texts: [{ text: `use ${REF}`, replaced: [{ start: 4, end: 4 + TOKEN.length, reference: REF, created: false, temporary }] }],
    },
  },
});

describe("prompt sealing", () => {
  test("off: read once per session, then no process at all", async ($, on) => {
    const { runs } = helper(on, { setting: answer({ sealing: "off" }) });
    const seen = sessionSees(on);
    await $.prompt.submit(TYPED);
    await $.prompt.submit(TYPED);
    expect(runs).toEqual([SETTING_RUN]);
    expect(seen).toEqual([{ text: TEXT, context: undefined }, { text: TEXT, context: undefined }]);
  });

  test("on: a temporary reference is replaced and the note rides along as context", async ($, on) => {
    const { runs, calls } = helper(on, { setting: ON, seal: sealedAnswer(true) });
    const seen = sessionSees(on);
    await $.prompt.submit(TYPED);
    await $.prompt.submit(TYPED);
    // One helper run for the session; every seal goes to the server directly.
    expect(runs).toEqual([SETTING_RUN]);
    expect(calls).toEqual([
      { url: SEAL_URL, body: { texts: [{ text: TEXT, enroll: true }] }, auth: "Bearer sk-test" },
      { url: SEAL_URL, body: { texts: [{ text: TEXT, enroll: true }] }, auth: "Bearer sk-test" },
    ]);
    // The session text rides along with the first sealed prompt only.
    expect(seen).toEqual([
      { text: `use ${REF}`, context: [sealNote([REF]), SESSION_TEXT] },
      { text: `use ${REF}`, context: [sealNote([REF])] },
    ]);
  });

  test("on: a saved reference is replaced without a note", async ($, on) => {
    helper(on, { setting: ON, seal: sealedAnswer(false) });
    const seen = sessionSees(on);
    await $.prompt.submit(TYPED);
    await $.prompt.submit(TYPED);
    expect(seen).toEqual([{ text: `use ${REF}`, context: [SESSION_TEXT] }, { text: `use ${REF}`, context: [] }]);
  });

  test("on: the session text comes again after a compaction", async ($, on) => {
    helper(on, { setting: ON, seal: { status: 200, json: { status: "ok", result: { revision: "r1", texts: [null] } } } });
    const seen = sessionSees(on);
    const summary = [{ role: "user" as const, text: "summary", toolUses: [] }];
    on("session.compact", () => ({ messages: summary }));
    await $.prompt.submit(TYPED);
    await $.prompt.submit(TYPED);
    await $.session.compact({ trigger: "manual", messages: summary } as never);
    await $.prompt.submit(TYPED);
    expect(seen.map((p) => p.context)).toEqual([[SESSION_TEXT], undefined, [SESSION_TEXT]]);
  });

  test("a server too old to seal stops the prompt without a call, until it is upgraded", async ($, on) => {
    let version = "0.5.6";
    const { calls } = helper(on, {
      setting: () => answer({ sealing: "on", enrollToolSecrets: false, server: SERVER, support: support(version) }),
      seal: sealedAnswer(false),
    });
    const seen = sessionSees(on);
    const res = await $.prompt.submit(TYPED);
    expect(calls).toEqual([]);
    expect(seen).toEqual([]);
    expect(res.drop).toContain("version 0.5.6, and sealing secrets needs 0.5.7 or newer");
    expect(res.drop).toContain("Upgrade the OpenViking server, or turn sealing off with sealSecrets: false");
    version = "0.5.7";
    await $.prompt.submit(TYPED);
    expect(calls.map((call) => call.url)).toEqual([SEAL_URL]);
    expect(seen.length).toBe(1);
  });

  test("a failed seal stops the prompt", async ($, on) => {
    helper(on, { setting: ON, seal: new Error("connection refused") });
    const seen = sessionSees(on);
    const res = await $.prompt.submit(TYPED);
    expect(seen).toEqual([]);
    expect(res.drop).toContain("sealing secrets failed (cannot reach the OpenViking server: ");
    expect(res.drop).toContain("OPENVIKING_SEAL_SECRETS=0");
  });

  test("no answer: a failed setting helper means off, without a message", async ($, on) => {
    const { runs, calls } = helper(on, { setting: { exitCode: 1, stdout: "" } });
    const seen = sessionSees(on);
    const lines = logged(on);
    await $.prompt.submit(TYPED);
    await $.prompt.submit(TYPED);
    expect(runs).toEqual([SETTING_RUN]);
    expect(calls).toEqual([]);
    expect(seen).toEqual([{ text: TEXT, context: undefined }, { text: TEXT, context: undefined }]);
    expect(lines).toEqual([]);
  });

  test("no answer: a surface without $.process means off, without a message", async ($, on) => {
    mock.env(on, {});
    const seen = sessionSees(on);
    const lines = logged(on);
    await $.prompt.submit(TYPED);
    expect(seen).toEqual([{ text: TEXT, context: undefined }]);
    expect(lines).toEqual([]);
  });

  test("unknown: OPENVIKING_SEAL_SECRETS=1 without an answer stops prompts", async ($, on) => {
    const { runs, calls } = helper(on, { setting: { exitCode: 1, stdout: "" }, env: { OPENVIKING_SEAL_SECRETS: "1" } });
    const seen = sessionSees(on);
    const first = await $.prompt.submit(TYPED);
    await $.prompt.submit(TYPED);
    // Each stopped prompt reads the setting again.
    expect(runs).toEqual([SETTING_RUN, SETTING_RUN]);
    expect(calls).toEqual([]);
    expect(seen).toEqual([]);
    expect(first.drop).toContain("the plugin's settings could not be read here");
    expect(first.drop).toContain("Use Claude Code where the plugin can run its setting helper");
  });

  test("unknown: OPENVIKING_SEAL_SECRETS=1 on a surface without $.process stops the prompt", async ($, on) => {
    mock.env(on, { OPENVIKING_SEAL_SECRETS: "1" });
    const seen = sessionSees(on);
    const res = await $.prompt.submit(TYPED);
    expect(seen).toEqual([]);
    expect(res.drop).toContain("the plugin's settings could not be read here");
  });

  test("OPENVIKING_SEAL_SECRETS=0 starts no process", async ($, on) => {
    const { runs, calls } = helper(on, { setting: new Error("must not read"), env: { OPENVIKING_SEAL_SECRETS: "0" } });
    const seen = sessionSees(on);
    await $.prompt.submit(TYPED);
    expect(runs).toEqual([]);
    expect(calls).toEqual([]);
    expect(seen).toEqual([{ text: TEXT, context: undefined }]);
  });
});

// ── tool calls and attachments ──────────────────────────────────────────

const VALUE = "ghp_REGISTEREDsecretVALUE1234567890abcd";
const SAVED = "{{ov_privacy:alice:viking://~/privacy/credential/github#/values/token}}";

// The server: VALUE is registered as SAVED.
function sealBody(body: { texts: { text: string; enroll?: boolean }[] }): Answer {
  const texts = body.texts.map(({ text }) => {
    const at = text.indexOf(VALUE);
    if (at < 0) return null;
    return {
      text: text.replace(VALUE, SAVED),
      replaced: [{ start: at, end: at + VALUE.length, reference: SAVED, created: false, temporary: false }],
    };
  });
  return { status: 200, json: { status: "ok", result: { revision: "r1", texts } } };
}
const RESOLVED: Answer = { status: 200, json: { status: "ok", result: { value: VALUE } } };

// The engine beneath the plugin: Bash records the command it ran and prints `out`.
function bash(on: On, out: (command: string) => string, isError = false) {
  const ran: string[] = [];
  on("tool.call", (_$, e) => {
    if (e.tool !== "Bash") throw new Error("unexpected tool");
    ran.push(e.command);
    const stdout = out(e.command);
    if (isError) return { isError: true as const, result: stdout, text: stdout };
    return { result: { stdout, stderr: "", interrupted: false }, text: stdout };
  });
  return ran;
}

describe("tool sealing", () => {
  test("a reference in the arguments runs with the value, and the result is sealed", async ($, on) => {
    const { calls } = helper(on, { setting: ON, seal: sealBody, resolve: RESOLVED });
    const ran = bash(on, (command) => `got ${command.slice(5)}`);
    const res = await $.tool.call({ tool: "Bash", command: `echo ${SAVED}` });
    expect(ran).toEqual([`echo ${VALUE}`]);
    expect(res.result).toEqual({ stdout: `got ${SAVED}`, stderr: "", interrupted: false });
    expect(calls.map((c) => c.url.split("/").at(-1))).toEqual(["resolve", "seal", "seal"]);
    // Tool output is not enrolled unless enrollToolSecrets is on.
    expect(calls[2]?.body).toEqual({ texts: [{ text: `got ${VALUE}`, enroll: false }] });
  });

  test("enrollToolSecrets sends the result with enroll", async ($, on) => {
    const { calls } = helper(on, {
      setting: answer({ sealing: "on", enrollToolSecrets: true, server: SERVER, support: support("0.5.7") }),
      seal: sealBody,
    });
    bash(on, () => "a plain output line");
    await $.tool.call({ tool: "Bash", command: "ls" });
    expect(calls).toEqual([
      { url: SEAL_URL, body: { texts: [{ text: "a plain output line", enroll: true }] }, auth: "Bearer sk-test" },
    ]);
  });

  test("an errored call's text is sealed", async ($, on) => {
    helper(on, { setting: ON, seal: sealBody });
    bash(on, () => `Exit code 1\nbad token ${VALUE}`, true);
    const res = await $.tool.call({ tool: "Bash", command: "false" });
    expect(JSON.stringify(res)).not.toContain(VALUE);
    expect(JSON.stringify(res)).toContain(SAVED);
  });

  test("a failed seal withholds the result", async ($, on) => {
    helper(on, { setting: ON, seal: { status: 500, json: {} } });
    const ran = bash(on, () => `token ${VALUE}`);
    const res = await $.tool.call({ tool: "Bash", command: "cat .env" });
    expect(ran).toEqual(["cat .env"]);
    expect(JSON.stringify(res)).not.toContain(VALUE);
    expect(JSON.stringify(res)).toContain("OpenViking withheld this tool result");
  });

  test("a failed resolve blocks the call", async ($, on) => {
    helper(on, { setting: ON, seal: sealBody, resolve: new Error("connection refused") });
    const ran = bash(on, () => "x");
    const res = await $.tool.call({ tool: "Bash", command: `echo ${SAVED}` });
    expect(ran).toEqual([]);
    expect(JSON.stringify(res)).toContain("OpenViking blocked this tool call");
  });

  test("off: tools run untouched with no server call", async ($, on) => {
    const { calls } = helper(on, { setting: answer({ sealing: "off" }) });
    const ran = bash(on, () => `token ${VALUE}`);
    const res = await $.tool.call({ tool: "Bash", command: `echo ${SAVED}` });
    expect(ran).toEqual([`echo ${SAVED}`]);
    expect(res.result).toEqual({ stdout: `token ${VALUE}`, stderr: "", interrupted: false });
    expect(calls).toEqual([]);
  });
});

// The engine beneath the plugin: the attachment as the model would read it.
function attachments(on: On) {
  const seen: string[] = [];
  on("prompt.attachment", (_$, e) => {
    seen.push(e.text);
    return { text: e.text };
  });
  return seen;
}

describe("attachment sealing", () => {
  test("a mentioned file's text is sealed without enroll", async ($, on) => {
    const { calls } = helper(on, { setting: ON, seal: sealBody });
    const seen = attachments(on);
    const res = await $.prompt.attachment({ type: "file", text: `KEY=${VALUE}`, origin: { kind: "engine" } });
    expect(res.text).toBe(`KEY=${SAVED}`);
    expect(seen).toEqual([`KEY=${SAVED}`]);
    expect(calls[0]?.body).toEqual({ texts: [{ text: `KEY=${VALUE}`, enroll: false }] });
  });

  test("a failed seal withholds the attachment", async ($, on) => {
    helper(on, { setting: ON, seal: { status: 503, json: {} } });
    const seen = attachments(on);
    const res = await $.prompt.attachment({ type: "file", text: `KEY=${VALUE}`, origin: { kind: "engine" } });
    expect(res.text).toContain("OpenViking withheld this attached content");
    expect(res.text).not.toContain(VALUE);
    expect(seen).toEqual([res.text]);
  });
});
