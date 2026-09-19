import test, { after, before, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { OVClient, archiveIdFromUri, archiveUriToRoot } from "../client.ts";

// Envelope shapes below are the ones recorded against the volc server:
//   fs/ls        -> { status: "ok", result: [{ uri, size, isDir, modTime, abstract }] }
//   content/read -> { status: "ok", result: "<text>" }
//   search/grep  -> { status: "ok", result: { matches: [{ line, uri, content }] } }
//   sessions/{id}-> { status: "ok", result: { session_id, uri, ... } }
//   not found    -> { status: "error", error: { code: "NOT_FOUND", message } }

const SID = "pi-sess-1";
const ROOT = "viking://user/default/sessions/pi-sess-1";
const ARCHIVE = `${ROOT}/history/archive_002`;

let originalFetch;
let calls;

before(() => {
  originalFetch = globalThis.fetch;
});

after(() => {
  globalThis.fetch = originalFetch;
});

beforeEach(() => {
  calls = [];
  globalThis.fetch = originalFetch;
});

/** Route the stub by URL substring; `handler` returns a body or { httpStatus, body }. */
function stubFetch(handler) {
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    const out = await handler(String(url), init);
    const httpStatus = out?.httpStatus ?? 200;
    const body = out?.httpStatus ? out.body : out;
    return new Response(JSON.stringify(body), {
      status: httpStatus,
      headers: { "Content-Type": "application/json" },
    });
  };
}

function makeClient() {
  return new OVClient({
    endpoint: "http://127.0.0.1:1933/",
    apiKey: "secret",
    account: "acct",
    user: "user",
    peerId: "peer",
    userAgent: "openviking-pi-xcm/0.1.0",
    commitKeepRecentCount: 10,
  });
}

const sessionOk = {
  status: "ok",
  result: { session_id: SID, uri: ROOT, message_count: 12, commit_count: 1 },
};
const notFound = {
  httpStatus: 404,
  body: { status: "error", error: { code: "NOT_FOUND", message: "not found" } },
};

// ---------- sessionRootUri / archiveUriToRoot ----------

test("sessionRootUri reads the canonical uri once and caches it", async () => {
  stubFetch((url) => {
    assert.ok(url.includes(`/api/v1/sessions/${SID}`));
    return sessionOk;
  });
  const client = makeClient();
  assert.equal(await client.sessionRootUri(SID), ROOT);
  assert.equal(await client.sessionRootUri(SID), ROOT);
  assert.equal(calls.length, 1, "second call must be served from the cache");
});

test("sessionRootUri falls back to the legacy alias and does not cache it", async () => {
  stubFetch(() => notFound);
  const client = makeClient();
  assert.equal(await client.sessionRootUri(SID), `viking://session/${SID}`);
  assert.equal(await client.sessionRootUri(SID), `viking://session/${SID}`);
  assert.equal(calls.length, 2, "a failed lookup must stay retryable");
});

test("sessionRootUri ignores a non-viking uri and never emits the ~ alias", async () => {
  stubFetch(() => ({ status: "ok", result: { session_id: SID, uri: "~/sessions/pi-sess-1" } }));
  const client = makeClient();
  const root = await client.sessionRootUri(SID);
  assert.equal(root, `viking://session/${SID}`);
  assert.ok(!root.includes("~"));
});

test("sessionRootUri normalizes a trailing slash and survives a transport failure", async () => {
  stubFetch(() => ({ status: "ok", result: { session_id: SID, uri: ` ${ROOT}/ ` } }));
  assert.equal(await makeClient().sessionRootUri(SID), ROOT);

  globalThis.fetch = async () => { throw new Error("ECONNREFUSED"); };
  assert.equal(await makeClient().sessionRootUri(SID), `viking://session/${SID}`);
});

test("archiveUriToRoot strips the archive segment", () => {
  assert.equal(archiveUriToRoot(ARCHIVE), ROOT);
  assert.equal(OVClient.archiveUriToRoot(`${ARCHIVE}/`), ROOT);
  assert.equal(makeClient().archiveUriToRoot(`${ARCHIVE}/messages.jsonl`), ROOT);
  assert.equal(archiveUriToRoot(`viking://session/${SID}/history/archive_010`), `viking://session/${SID}`);
  assert.equal(archiveUriToRoot(ROOT), ROOT, "a root passes through unchanged");
  assert.equal(archiveIdFromUri(`${ARCHIVE}/messages.jsonl`), "archive_002");
  assert.equal(archiveIdFromUri(ROOT), "");
});

// ---------- readArchiveOverview ----------

test("readArchiveOverview requests the encoded .overview.md uri", async () => {
  stubFetch(() => ({ status: "ok", result: "# Working Memory\n\n## Session Title\nx" }));
  const text = await makeClient().readArchiveOverview(ARCHIVE);
  assert.equal(text, "# Working Memory\n\n## Session Title\nx");
  assert.equal(calls.length, 1);
  const url = calls[0].url;
  assert.ok(url.startsWith("http://127.0.0.1:1933/api/v1/content/read?uri="));
  assert.ok(url.includes(encodeURIComponent(`${ARCHIVE}/.overview.md`)));
  assert.ok(url.includes("%3A%2F%2F"), "the uri query must be percent-encoded");
});

test("readArchiveOverview strips a leading YAML frontmatter block", async () => {
  stubFetch(() => ({
    status: "ok",
    result: "---\ntitle: archive_002\ngenerated: 2026-09-10\n---\n# Working Memory\nbody\n",
  }));
  assert.equal(await makeClient().readArchiveOverview(ARCHIVE), "# Working Memory\nbody\n");
});

test("readArchiveOverview keeps a body whose --- is not frontmatter", async () => {
  const body = "# Working Memory\n\n---\n\nnotes";
  stubFetch(() => ({ status: "ok", result: body }));
  assert.equal(await makeClient().readArchiveOverview(ARCHIVE), body);
});

// The real sidecar: openviking/storage/abstract_overview.py::render_abstract_overview
// writes `---\n<yaml>\n---\n\n<body>\n`, and content/read only strips frontmatter
// for memory URIs, so a session archive answers with the OKF document verbatim.
test("readArchiveOverview strips the OKF sidecar the server actually writes", async () => {
  const stored = [
    "---",
    "generated_by:",
    "  component: Session",
    "  trigger: archive_summary",
    `directory: ${ARCHIVE}`,
    "---",
    "",
    "# Working Memory",
    "",
    "## Session Title",
    "Phase one",
    "",
  ].join("\n");
  stubFetch(() => ({ status: "ok", result: stored }));
  const text = await makeClient().readArchiveOverview(ARCHIVE);
  assert.equal(text, "# Working Memory\n\n## Session Title\nPhase one\n");
  assert.ok(!text.startsWith("\n"), "the blank line after the frontmatter must go too");
  assert.ok(!text.includes("generated_by"));
});

test("readArchiveOverview treats a body-less sidecar as not ready", async () => {
  stubFetch(() => ({ status: "ok", result: "---\ndirectory: x\n---\n\n" }));
  assert.equal(await makeClient().readArchiveOverview(ARCHIVE), null);
  stubFetch(() => ({ status: "ok", result: "   \n" }));
  assert.equal(await makeClient().readArchiveOverview(ARCHIVE), null);
});

test("readArchiveOverview returns null while phase 2 is still running", async () => {
  stubFetch(() => notFound);
  assert.equal(await makeClient().readArchiveOverview(ARCHIVE), null);
});

test("readArchiveOverview returns null when fetch itself blows up", async () => {
  globalThis.fetch = async () => { throw new Error("ECONNREFUSED"); };
  assert.equal(await makeClient().readArchiveOverview(ARCHIVE), null);
  assert.equal(await makeClient().readArchiveOverview(""), null);
});

// ---------- readArchiveMessages ----------

test("readArchiveMessages parses JSONL and keeps a malformed line as a placeholder", async () => {
  const lines = [
    JSON.stringify({ id: "m1", role: "user", parts: [{ type: "text", text: "hello" }], created_at: "2026-09-10T06:00:00Z" }),
    "{ this is not json",
    JSON.stringify({
      id: "m2",
      role: "assistant",
      parts: [
        { type: "text", text: "running it" },
        { type: "tool_result", name: "bash", output: "exit 0" },
      ],
      created_at: "2026-09-10T06:00:05Z",
    }),
    "",
    JSON.stringify({ id: "m3", role: "user", content: "plain content" }),
  ].join("\n");
  stubFetch(() => ({ status: "ok", result: lines }));

  const msgs = await makeClient().readArchiveMessages(ARCHIVE);
  // Five, not three: `search_contents` turns a grep hit's line number into an
  // item index, so every line of the file keeps its slot — a malformed one and
  // a blank one included — instead of shifting every pointer after it by one.
  assert.equal(msgs.length, 5);
  assert.ok(calls[0].url.includes(encodeURIComponent(`${ARCHIVE}/messages.jsonl`)));

  assert.deepEqual(msgs[0], {
    id: "m1",
    role: "user",
    text: "hello",
    parts: [{ type: "text", text: "hello" }],
    created_at: "2026-09-10T06:00:00Z",
  });
  assert.deepEqual(msgs[1], {
    id: "", role: "unreadable", text: "(unreadable archive line)", parts: [], created_at: "",
  });
  assert.equal(msgs[2].text, "running it\n[tool bash] exit 0");
  assert.equal(msgs[2].parts.length, 2);
  assert.deepEqual(msgs[3], {
    id: "", role: "blank", text: "(blank archive line)", parts: [], created_at: "",
  });
  assert.equal(msgs[4].text, "plain content");
  assert.equal(msgs[4].created_at, "");
});

test("readArchiveMessages returns null when the file is unreadable", async () => {
  stubFetch(() => notFound);
  assert.equal(await makeClient().readArchiveMessages(ARCHIVE), null);
});

// Part shapes below are exactly what openviking/message/message.py::_part_to_dict
// serializes into messages.jsonl: a tool call carries tool_input only, a tool
// result carries tool_output, and neither carries `output`/`text`.
test("readArchiveMessages renders the server's real tool/context/image parts", async () => {
  const lines = [
    JSON.stringify({
      id: "m1",
      role: "assistant",
      parts: [{
        type: "tool",
        tool_id: "call_1",
        tool_name: "bash",
        tool_uri: "",
        skill_uri: "",
        tool_status: "completed",
        tool_input: { command: "ls -la" },
      }],
      created_at: "2026-09-10T06:00:00Z",
    }),
    JSON.stringify({
      id: "m2",
      role: "user",
      parts: [{
        type: "tool",
        tool_name: "bash",
        tool_status: "completed",
        tool_input: { command: "ls -la" },
        tool_output: "total 0",
      }],
      created_at: "2026-09-10T06:00:01Z",
    }),
    JSON.stringify({
      id: "m3",
      role: "user",
      parts: [
        { type: "context", uri: "viking://user/default/memories/x", context_type: "memory", abstract: "codename ZEPHYR-9942" },
        { type: "image", image_url: { url: "data:image/png;base64,AAAA" } },
      ],
      created_at: "2026-09-10T06:00:02Z",
    }),
    JSON.stringify({
      id: "m4",
      role: "assistant",
      parts: [{ type: "tool", tool_name: "", tool_status: "error" }],
      created_at: "2026-09-10T06:00:03Z",
    }),
    JSON.stringify({
      id: "m5",
      role: "user",
      parts: [{
        type: "tool",
        tool_name: "read_file",
        tool_status: "completed",
        tool_output: "",
        tool_output_ref: `${ARCHIVE}/tools/call_9.txt`,
      }],
      created_at: "2026-09-10T06:00:04Z",
    }),
  ].join("\n");
  stubFetch(() => ({ status: "ok", result: lines }));

  const msgs = await makeClient().readArchiveMessages(ARCHIVE);
  assert.equal(msgs.length, 5);
  assert.equal(msgs[0].text, '[tool bash] {"command":"ls -la"}');
  assert.equal(msgs[1].text, '[tool bash] {"command":"ls -la"} total 0');
  assert.equal(
    msgs[2].text,
    "[context viking://user/default/memories/x] codename ZEPHYR-9942\n[image]",
  );
  assert.equal(msgs[3].text, "[tool unknown]", "a bare tool part must not read back blank");
  assert.equal(msgs[4].text, `[tool read_file] <output stored at ${ARCHIVE}/tools/call_9.txt>`);
  for (const m of msgs) assert.notEqual(m.text, "", "no archived part may vanish");
});

test("readArchiveMessages tolerates a non-array parts field and a JSON scalar line", async () => {
  const lines = [
    JSON.stringify({ id: "m1", role: "user", parts: "oops", content: "fallback text" }),
    "42",
    JSON.stringify(["not", "an", "object"]),
    JSON.stringify(null),
    JSON.stringify({ role: "assistant", parts: [{ type: "text", text: "kept" }] }),
  ].join("\r\n");
  stubFetch(() => ({ status: "ok", result: lines }));

  const msgs = await makeClient().readArchiveMessages(ARCHIVE);
  // The three lines that are not JSON objects keep their slots as placeholders.
  assert.equal(msgs.length, 5);
  assert.deepEqual(msgs[0], {
    id: "m1", role: "user", text: "fallback text", parts: [], created_at: "",
  });
  for (const i of [1, 2, 3]) assert.equal(msgs[i].role, "unreadable");
  assert.equal(msgs[4].id, "", "a missing id becomes an empty string, never 'undefined'");
  assert.equal(msgs[4].text, "kept");
});

test("readArchiveMessages returns [] for an archive that is present but empty", async () => {
  stubFetch(() => ({ status: "ok", result: "" }));
  assert.deepEqual(await makeClient().readArchiveMessages(ARCHIVE), []);
  assert.equal(await makeClient().readArchiveMessages("   "), null);
});

test("readArchiveMessages keeps blank lines numbered and drops only the final newline", async () => {
  // Two blank lines: the trailing newline terminates line 2 rather than opening
  // a line 3, so the file is two items, both placeholders. Anything else would
  // move `line - 1` off the item a grep hit points at.
  stubFetch(() => ({ status: "ok", result: "\n\n" }));
  const blanks = await makeClient().readArchiveMessages(ARCHIVE);
  assert.equal(blanks.length, 2);
  for (const item of blanks) {
    assert.deepEqual(item, {
      id: "", role: "blank", text: "(blank archive line)", parts: [], created_at: "",
    });
  }

  // A blank line between two real messages keeps the second one at line 3.
  const lines = [
    JSON.stringify({ id: "m1", role: "user", content: "one" }),
    "",
    JSON.stringify({ id: "m2", role: "user", content: "two" }),
    "",
  ].join("\n");
  stubFetch(() => ({ status: "ok", result: lines }));
  const msgs = await makeClient().readArchiveMessages(ARCHIVE);
  assert.equal(msgs.length, 3, "the trailing newline is not a fourth line");
  assert.equal(msgs[0].id, "m1");
  assert.equal(msgs[1].role, "blank");
  assert.equal(msgs[2].id, "m2");
});

// ---------- listSessionArchives ----------

test("listSessionArchives filters to archive_NNN and orders newest first", async () => {
  stubFetch((url) => {
    if (url.includes("/api/v1/sessions/")) return sessionOk;
    return {
      status: "ok",
      result: [
        { uri: `${ROOT}/history/archive_001`, size: 0, isDir: true, modTime: "2026-09-10T05:00:00Z", abstract: "# Working Memory" },
        { uri: `${ROOT}/history/archive_003`, size: 0, isDir: true, modTime: "2026-09-10T07:00:00Z", abstract: "[Directory abstract is not ready]" },
        { uri: `${ROOT}/history/.meta.json`, size: 12, isDir: false, modTime: "", abstract: "" },
        { uri: `${ROOT}/history/archive_002`, size: 0, isDir: true, modTime: "2026-09-10T06:00:00Z", abstract: "# Working Memory" },
      ],
    };
  });

  const rows = await makeClient().listSessionArchives(SID);
  assert.deepEqual(rows.map(r => r.archiveId), ["archive_003", "archive_002", "archive_001"]);
  assert.equal(rows[0].uri, `${ROOT}/history/archive_003`);
  assert.equal(rows[0].modTime, "2026-09-10T07:00:00Z");
  assert.equal(rows[2].abstract, "# Working Memory");

  const lsUrl = calls[1].url;
  assert.ok(lsUrl.startsWith("http://127.0.0.1:1933/api/v1/fs/ls?uri="));
  assert.ok(lsUrl.includes(encodeURIComponent(`${ROOT}/history`)));
  assert.ok(lsUrl.includes("sort_by=name"));
  assert.ok(lsUrl.includes("sort_order=desc"));
});

test("listSessionArchives skips a non-directory entry named like an archive", async () => {
  stubFetch((url) => {
    if (url.includes("/api/v1/sessions/")) return sessionOk;
    return {
      status: "ok",
      result: [
        { uri: `${ROOT}/history/archive_004`, isDir: false, modTime: "", abstract: "" },
        { uri: `${ROOT}/history/archive_002`, isDir: true, modTime: "", abstract: "" },
        { uri: "", name: "archive_001", isDir: true, modTime: "", abstract: "" },
        "not an object",
      ],
    };
  });
  const rows = await makeClient().listSessionArchives(SID);
  assert.deepEqual(rows.map(r => r.archiveId), ["archive_002", "archive_001"]);
});

test("listSessionArchives uses the legacy root when the session lookup fails", async () => {
  stubFetch((url) => (url.includes("/api/v1/sessions/") ? notFound : { status: "ok", result: [] }));
  await makeClient().listSessionArchives(SID);
  assert.ok(calls[1].url.includes(encodeURIComponent(`viking://session/${SID}/history`)));
  assert.ok(!calls[1].url.includes("~"));
});

test("listSessionArchives tells an empty history apart from an unreadable one", async () => {
  // A session that never committed has no `history` directory: an empty list.
  stubFetch((url) => (url.includes("/api/v1/sessions/") ? sessionOk : notFound));
  assert.deepEqual(await makeClient().listSessionArchives(SID), []);

  // A blocked or broken listing is null — `history` must not report it to the
  // model as "this session has no archived windows".
  for (const failure of [
    { httpStatus: 403, body: { status: "error", error: { code: "ApiBlocked", message: "blocked" } } },
    { httpStatus: 500, body: { status: "error", error: { message: "boom" } } },
    { status: "ok", result: { not: "an array" } },
  ]) {
    stubFetch((url) => (url.includes("/api/v1/sessions/") ? sessionOk : failure));
    assert.equal(await makeClient().listSessionArchives(SID), null);
  }
});

// ---------- grepSessionArchives ----------

test("grepSessionArchives posts a regex over the whole history and parses matches", async () => {
  stubFetch((url) => {
    if (url.includes("/api/v1/sessions/")) return sessionOk;
    return {
      status: "ok",
      result: {
        matches: [
          { line: 14, uri: `${ROOT}/history/archive_002/messages.jsonl`, content: "codename ZEPHYR-9942" },
          { line: 3, uri: `${ROOT}/history/archive_001/.overview.md`, content: "## Key Facts" },
          { line: "bad", uri: "", content: 7 },
        ],
      },
    };
  });

  const matches = await makeClient().grepSessionArchives(SID, "ZEPHYR.\\d+");
  assert.deepEqual(matches[0], {
    archiveId: "archive_002",
    file: "messages.jsonl",
    line: 14,
    content: "codename ZEPHYR-9942",
  });
  assert.deepEqual(matches[1], {
    archiveId: "archive_001",
    file: ".overview.md",
    line: 3,
    content: "## Key Facts",
  });
  assert.deepEqual(matches[2], { archiveId: "", file: "", line: 0, content: "" });

  const grepCall = calls[1];
  assert.equal(grepCall.url, "http://127.0.0.1:1933/api/v1/search/grep");
  assert.equal(grepCall.init.method, "POST");
  assert.deepEqual(JSON.parse(grepCall.init.body), {
    uri: `${ROOT}/history`,
    pattern: "ZEPHYR.\\d+",
    case_insensitive: true,
    node_limit: 256,
  });
});

test("grepSessionArchives scopes to one archive, escapes literals, honours options", async () => {
  stubFetch((url) => (url.includes("/api/v1/sessions/") ? sessionOk : { status: "ok", result: { matches: [] } }));
  const client = makeClient();
  await client.grepSessionArchives(SID, "a.b(c)", {
    archiveId: "archive_002",
    caseInsensitive: false,
    nodeLimit: 32,
    literal: true,
  });
  assert.deepEqual(JSON.parse(calls[1].init.body), {
    uri: `${ROOT}/history/archive_002`,
    pattern: "a\\.b\\(c\\)",
    case_insensitive: false,
    node_limit: 32,
  });

  await client.grepSessionArchives(SID, "a.b(c)", { archiveId: "../../etc" });
  assert.equal(JSON.parse(calls[2].init.body).uri, `${ROOT}/history`, "a bogus archive id must not escape the root");
});

test("grepSessionArchives accepts a bare array body and clamps node_limit", async () => {
  stubFetch((url) => {
    if (url.includes("/api/v1/sessions/")) return sessionOk;
    return { status: "ok", result: [{ line: 2, uri: `${ROOT}/history/archive_001/messages.jsonl`, content: "x" }, null] };
  });
  const client = makeClient();
  const matches = await client.grepSessionArchives(SID, "x", { nodeLimit: 99999 });
  assert.equal(matches.length, 1);
  assert.equal(matches[0].archiveId, "archive_001");
  assert.equal(JSON.parse(calls[1].init.body).node_limit, 4096);

  await client.grepSessionArchives(SID, "x", { nodeLimit: 0 });
  assert.equal(JSON.parse(calls[2].init.body).node_limit, 1);
  await client.grepSessionArchives(SID, "x", { nodeLimit: Number.NaN });
  assert.equal(JSON.parse(calls[3].init.body).node_limit, 256);
});

test("grepSessionArchives keeps the scoped archive id when a match has no uri", async () => {
  stubFetch((url) => {
    if (url.includes("/api/v1/sessions/")) return sessionOk;
    return { status: "ok", result: { matches: [{ line: 1, content: "hit" }] } };
  });
  const matches = await makeClient().grepSessionArchives(SID, "hit", { archiveId: "archive_007" });
  assert.deepEqual(matches[0], { archiveId: "archive_007", file: "", line: 1, content: "hit" });
});

test("grepSessionArchives returns [] on failure and for an empty pattern", async () => {
  stubFetch((url) => (url.includes("/api/v1/sessions/") ? sessionOk : notFound));
  const client = makeClient();
  assert.deepEqual(await client.grepSessionArchives(SID, "x"), []);
  const before = calls.length;
  assert.deepEqual(await client.grepSessionArchives(SID, ""), []);
  assert.equal(calls.length, before, "an empty pattern must not reach the server");
});

// ---------- getTask ----------

test("getTask reports phase 2 status, stage and error", async () => {
  stubFetch(() => ({ status: "ok", result: { task_id: "t-1", status: "running", stage: "extraction" } }));
  const running = await makeClient().getTask("t-1");
  assert.deepEqual(running, { status: "running", stage: "extraction" });
  assert.equal(calls[0].url, "http://127.0.0.1:1933/api/v1/tasks/t-1");

  stubFetch(() => ({ status: "ok", result: { status: "failed", error: "vlm timeout" } }));
  assert.deepEqual(await makeClient().getTask("t-1"), { status: "failed", error: "vlm timeout" });
});

// TaskRecord.to_dict() always emits stage and error, null when unset.
test("getTask ignores the null stage/error the tracker always serializes", async () => {
  stubFetch(() => ({
    status: "ok",
    result: {
      task_id: "t-2",
      task_type: "session_commit",
      status: "completed",
      stage: null,
      error: null,
      meta: {},
      result: { archived: 12 },
      created_at: 1,
      updated_at: 2,
    },
  }));
  assert.deepEqual(await makeClient().getTask("t-2"), { status: "completed" });
});

test("getTask survives a non-string error payload and a dead connection", async () => {
  stubFetch(() => ({ status: "ok", result: { status: "failed", error: { code: "VLM", msg: "boom" } } }));
  const t = await makeClient().getTask("t-3");
  assert.equal(t.status, "failed");
  assert.equal(t.error, '{"code":"VLM","msg":"boom"}');

  globalThis.fetch = async () => { throw new Error("ECONNREFUSED"); };
  assert.equal(await makeClient().getTask("t-3"), null);
});

test("getTask returns null on failure or an empty id", async () => {
  stubFetch(() => notFound);
  const client = makeClient();
  assert.equal(await client.getTask("t-1"), null);
  const before = calls.length;
  assert.equal(await client.getTask(""), null);
  assert.equal(calls.length, before);
});

// ---------- regressions on the methods the archive work was not allowed to change ----------

test("commitSessionResponse still returns the envelope, widened fields included", async () => {
  stubFetch(() => ({
    status: "ok",
    result: { session_id: SID, status: "accepted", task_id: "t-9", archive_uri: ARCHIVE, archived: true },
    trace_id: "trace-42",
  }));
  const res = await makeClient().commitSessionResponse(SID, 0);
  assert.equal(res.result.status, "accepted");
  assert.equal(res.result.archive_uri, ARCHIVE);
  assert.equal(res.result.archived, true);
  assert.equal(res.result.trace_id, "trace-42", "the envelope trace id is still back-filled");
  assert.deepEqual(JSON.parse(calls[0].init.body), { keep_recent_count: 0 });

  // A skipped commit sends archive_uri: null — callers must see it, not undefined.
  stubFetch(() => ({
    status: "ok",
    result: { session_id: SID, status: "skipped", archive_uri: null, archived: false, reason: "no_messages" },
  }));
  const skipped = (await makeClient().commitSessionResponse(SID, 0)).result;
  assert.equal(skipped.status, "skipped");
  assert.equal(skipped.archive_uri, null);
  assert.equal(skipped.reason, "no_messages");
});

test("ls, readContent and health keep their pre-existing behaviour", async () => {
  stubFetch((url) => {
    if (url.includes("/health")) return { status: "ok", result: {} };
    if (url.includes("/content/read")) return { status: "ok", result: "file body" };
    return { status: "ok", result: [{ uri: `${ROOT}/history`, isDir: true, size: 0, mode: 0, modTime: "t", abstract: "a" }] };
  });
  const client = makeClient();
  assert.equal(await client.health(), true);
  assert.equal(client.connected, true);
  assert.equal(await client.readContent(`${ARCHIVE}/messages.jsonl`), "file body");
  const rows = await client.ls(ROOT);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, "history", "name still falls back to the uri basename");
});

test("fetchJSON reports a transport failure instead of throwing, and still times out", async () => {
  globalThis.fetch = async () => { throw new Error("ECONNREFUSED"); };
  const client = makeClient();
  const dead = await client.fetchJSON("/api/v1/health");
  assert.equal(dead.ok, false);
  assert.equal(dead.status, 0);
  assert.match(dead.error.message, /ECONNREFUSED/);

  globalThis.fetch = (url, init) => new Promise((_resolve, reject) => {
    init.signal.addEventListener("abort", () => reject(new Error("aborted")));
  });
  const started = Date.now();
  const timedOut = await client.fetchJSON("/api/v1/health", undefined, 20);
  assert.equal(timedOut.ok, false);
  assert.ok(Date.now() - started < 5000, "the abort timer must still fire");
});
