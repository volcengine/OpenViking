/**
 * The history import script the onboarding guide hands to agents.
 *
 * It is published as one file, so the workspace and connection rules it
 * carries are copies; the parity tests below hold them to the modules the
 * plugins run.
 */

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, mkdtempSync, realpathSync, rmSync, utimesSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  cleanText,
  codexNativeId,
  liveSessionId,
  main,
  parseClaudeSession,
  parseCodexSession,
  parseSince,
  redactSecrets,
  resolveConnection as scriptConnection,
  resolvePeer,
  sessionId,
} from "./import-history.mjs";
import { HOSTS as HOOK_HOSTS } from "../agent-hook-plugin/hosts/index.mjs";
import { deriveDshSessionId } from "../dsh-memory-plugin/session-id.mjs";
import { deriveAgentSessionId } from "./lib/agent-hook-runtime.mjs";
import { resolveConnection as libConnection } from "./lib/credentials.mjs";
import { buildPluginConfig } from "./lib/plugin-config.mjs";
import { deriveCodexSessionId, deriveHarnessSessionId } from "./lib/session-model.mjs";
import { resolveEffectivePeerId } from "./lib/workspace-peer.mjs";

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), "import-history.mjs");
const NOW = Date.parse("2026-10-13T12:00:00Z");
const HOUR = 3_600_000;
const DAY = 24 * HOUR;

function tempRoot(t) {
  const root = realpathSync(mkdtempSync(join(tmpdir(), "ov-import-history-")));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  return root;
}

function writeJsonl(path, records, mtimeMs) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${records.map((r) => JSON.stringify(r)).join("\n")}\n`);
  if (mtimeMs) utimesSync(path, new Date(mtimeMs), new Date(mtimeMs));
}

function gitRepo(dir, remote = "") {
  mkdirSync(join(dir, ".git"), { recursive: true });
  const origin = remote ? `[remote "origin"]\n\turl = ${remote}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n` : "";
  writeFileSync(join(dir, ".git", "config"), `[core]\n\trepositoryformatversion = 0\n${origin}`);
}

const iso = (ms) => new Date(ms).toISOString();

// The record shapes Claude Code 2.1 writes: every line carries cwd and a
// timestamp, one line per content block, tool results come back as user lines.
function claudeTranscript(sessionId, cwd, start, turns) {
  const records = [
    { type: "queue-operation", operation: "enqueue", sessionId, timestamp: iso(start) },
    { type: "attachment", cwd, sessionId, timestamp: iso(start), attachment: { type: "hook_additional_context" }, isSidechain: false },
  ];
  let at = start;
  for (const turn of turns) {
    at += 60_000;
    const base = { cwd, sessionId, timestamp: iso(at), isSidechain: false, userType: "external", gitBranch: "main", version: "2.1.296" };
    if (turn.user !== undefined) records.push({ ...base, type: "user", message: { role: "user", content: turn.user } });
    if (turn.tool) {
      records.push({ ...base, type: "assistant", message: { role: "assistant", model: "claude-sonnet-5", content: [{ type: "tool_use", id: "t1", name: "Bash", input: { command: turn.tool } }] } });
      records.push({ ...base, type: "user", message: { role: "user", content: [{ type: "tool_result", tool_use_id: "t1", content: "tool output that must not be imported" }] } });
    }
    if (turn.assistant !== undefined) records.push({ ...base, type: "assistant", message: { role: "assistant", model: "claude-sonnet-5", content: [{ type: "text", text: turn.assistant }] } });
  }
  return records;
}

// The rollout shape Codex 0.146 writes.
function codexRollout(id, cwd, start, turns) {
  const records = [
    { timestamp: iso(start), type: "session_meta", payload: { id, session_id: id, cwd, source: "cli", thread_source: "user", originator: "codex_cli_rs", timestamp: iso(start) } },
    { timestamp: iso(start), type: "response_item", payload: { type: "message", role: "developer", content: [{ type: "input_text", text: "<permissions instructions>sandbox</permissions instructions>" }] } },
    { timestamp: iso(start), type: "response_item", payload: { type: "message", role: "user", content: [{ type: "input_text", text: `<environment_context>\n  <cwd>${cwd}</cwd>\n</environment_context>` }] } },
  ];
  let at = start;
  for (const turn of turns) {
    at += 60_000;
    records.push({ timestamp: iso(at), type: "response_item", payload: { type: "message", role: "user", content: [{ type: "input_text", text: turn.user }] } });
    records.push({ timestamp: iso(at), type: "event_msg", payload: { type: "user_message", message: turn.user } });
    records.push({ timestamp: iso(at + 1000), type: "response_item", payload: { type: "reasoning", summary: [] } });
    records.push({ timestamp: iso(at + 2000), type: "response_item", payload: { type: "custom_tool_call", name: "exec", input: "ls" } });
    records.push({ timestamp: iso(at + 3000), type: "response_item", payload: { type: "custom_tool_call_output", output: "secret tool output" } });
    records.push({ timestamp: iso(at + 4000), type: "response_item", payload: { type: "message", role: "assistant", content: [{ type: "output_text", text: turn.assistant }] } });
  }
  return records;
}

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

test("Claude Code: keeps the conversation text, drops tool traffic, host notes and side chains", (t) => {
  const root = tempRoot(t);
  const path = join(root, "s.jsonl");
  const records = claudeTranscript("s", "/work/app", NOW - DAY, [
    { user: "Fix the flaky login test<system-reminder>internal note</system-reminder>", tool: "npm test", assistant: "The race is in the token refresh." },
    { user: "<command-name>/clear</command-name><command-message>clear</command-message>" },
    { assistant: "Patched it with a lock." },
  ]);
  records.push({ type: "user", cwd: "/work/app", timestamp: iso(NOW), isSidechain: true, message: { role: "user", content: "subagent prompt" } });
  records.push({ type: "user", cwd: "/work/app", timestamp: iso(NOW), isMeta: true, message: { role: "user", content: "meta" } });
  records.push({ type: "assistant", cwd: "/work/app", timestamp: iso(NOW), message: { role: "assistant", model: "<synthetic>", content: [{ type: "text", text: "API Error" }] } });
  writeJsonl(path, records);

  const session = parseClaudeSession(path);
  assert.equal(session.nativeId, "s");
  assert.equal(session.cwd, "/work/app");
  assert.deepEqual(session.messages.map((m) => [m.role, m.text]), [
    ["user", "Fix the flaky login test"],
    ["assistant", "The race is in the token refresh."],
    ["assistant", "Patched it with a lock."],
  ]);
  assert.equal(session.messages[0].createdAt, iso(NOW - DAY + 60_000));
  assert.doesNotMatch(JSON.stringify(session), /tool output|subagent prompt|internal note|API Error/);
});

test("Codex: user and assistant text only, preambles dropped, fork files keep their own id", (t) => {
  const root = tempRoot(t);
  const id = "01a13469-3ea4-7dd0-8eb5-4e1aa971da6b";
  const path = join(root, "2026", "10", "12", `rollout-2026-10-12T08-00-00-${id}.jsonl`);
  writeJsonl(path, codexRollout(id, "/work/app", NOW - DAY, [{ user: "Use bazelisk here", assistant: "Noted, bazelisk it is." }]));

  const session = parseCodexSession(path);
  assert.equal(session.nativeId, id);
  assert.equal(session.liveId, id);
  assert.equal(session.cwd, "/work/app");
  assert.deepEqual(session.messages.map((m) => [m.role, m.text]), [["user", "Use bazelisk here"], ["assistant", "Noted, bazelisk it is."]]);
  assert.doesNotMatch(JSON.stringify(session), /secret tool output|permissions instructions|environment_context/);

  const fork = "0199aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee";
  assert.equal(codexNativeId(id, `/x/rollout-2026-10-12T08-00-00-${id}_${fork}.jsonl`), `${id}_${fork}`);
});

test("cleanText strips what hosts and plugins inject", () => {
  assert.equal(cleanText("<openviking-context>recalled</openviking-context>\nreal question"), "real question");
  assert.equal(cleanText("# AGENTS.md instructions for /repo\n\n<INSTRUCTIONS>\nrules\n</INSTRUCTIONS>"), "");
  assert.equal(cleanText("[Request interrupted by user]"), "");
});

// ---------------------------------------------------------------------------
// Redaction
// ---------------------------------------------------------------------------

test("secrets are redacted before anything is sent", () => {
  const cases = [
    "curl -fsSL https://openviking.ai/install | bash -s -- --yes --api-key ovk-abcdef123456",
    "run it with --api-key=\"ovk-abcdef123456\" please",
    "export OPENVIKING_API_KEY=ovk-abcdef123456",
    "Authorization: Bearer ovk-abcdef123456",
    '{"api_key": "ovk-abcdef123456"}',
    "my key is sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX",
    "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "AWS AKIAABCDEFGHIJKLMNOP",
  ];
  for (const text of cases) {
    const out = redactSecrets(text);
    assert.match(out, /\[REDACTED\]/, text);
    assert.doesNotMatch(out, /abcdef123456|ABCDEFGHIJKLMNOPQRSTUVWX|ABCDEFGHIJKLMNOPQRSTUVWXYZ0123|AKIAABCDEFGHIJKLMNOP/, text);
  }
  // The configured key, wherever it shows up and whatever it looks like.
  assert.equal(redactSecrets("pasted plainkey987654 here", ["plainkey987654"]), "pasted [REDACTED] here");
  // Ordinary prose and code survive.
  for (const text of ["The token refresh races with logout.", "const tokens = countTokens(text);", "Set the password field to required."]) {
    assert.equal(redactSecrets(text), text);
  }
});

// ---------------------------------------------------------------------------
// Parity with the plugin modules
// ---------------------------------------------------------------------------

function isolatedEnv(root, extra = {}) {
  return {
    HOME: join(root, "home"),
    OPENVIKING_HOME: join(root, "home", ".openviking"),
    OPENVIKING_STATE_DIR: join(root, "state"),
    OPENVIKING_CLI_CONFIG_FILE: join(root, "ovcli.conf"),
    OPENVIKING_CONFIG_FILE: join(root, "missing-ov.conf"),
    ...extra,
  };
}

test("the peer matches what the plugins derive for the same directory", (t) => {
  const root = tempRoot(t);
  mkdirSync(join(root, "home"), { recursive: true });
  const remote = join(root, "remote");
  gitRepo(remote, "git@github.com:Volcengine/OpenViking.git");
  mkdirSync(join(remote, "src", "deep"), { recursive: true });
  const bare = join(root, "bare");
  gitRepo(bare);
  const plain = join(root, "plain");
  mkdirSync(plain);
  const marked = join(root, "marked");
  gitRepo(marked, "https://example.com/team/marked.git");
  mkdirSync(join(marked, ".openviking"));
  writeFileSync(join(marked, ".openviking", "config.json"), JSON.stringify({ version: 1, peer: { id: "team-peer" } }));
  const sourced = join(root, "sourced");
  gitRepo(sourced, "https://example.com/team/sourced.git");
  mkdirSync(join(sourced, ".openviking"));
  writeFileSync(join(sourced, ".openviking", "config.local.json"), JSON.stringify({ version: 1, peer: { source: "team-{dir}" } }));
  const unversioned = join(root, "unversioned");
  gitRepo(unversioned, "https://example.com/team/unversioned.git");
  mkdirSync(join(unversioned, ".openviking"));
  writeFileSync(join(unversioned, ".openviking", "config.json"), JSON.stringify({ peer: { id: "ignored" } }));
  const registered = join(root, "registered");
  gitRepo(registered, "https://example.com/team/registered.git");
  const registry = join(root, "home", ".openviking", "workspaces");
  mkdirSync(registry, { recursive: true });
  const slot = `registered-${createHash("sha256").update("remote:example.com/team/registered").digest("hex").slice(0, 12)}.json`;
  writeFileSync(join(registry, slot), JSON.stringify({ version: 1, identity: "remote:example.com/team/registered", peer: { id: "machine-peer" } }));
  const worktree = join(root, "worktree");
  mkdirSync(join(remote, ".git", "worktrees", "wt"), { recursive: true });
  writeFileSync(join(remote, ".git", "worktrees", "wt", "commondir"), "../..\n");
  mkdirSync(worktree);
  writeFileSync(join(worktree, ".git"), `gitdir: ${join(remote, ".git", "worktrees", "wt")}\n`);

  const ovcli = (body) => writeFileSync(join(root, "ovcli.conf"), JSON.stringify(body));
  const scenarios = [
    { name: "remote", cwd: remote },
    { name: "subdirectory", cwd: join(remote, "src", "deep") },
    { name: "worktree", cwd: worktree },
    { name: "no remote", cwd: bare },
    { name: "not a repository", cwd: plain },
    { name: "workspace peer.id", cwd: marked },
    { name: "workspace peer.source", cwd: sourced },
    { name: "unversioned workspace file", cwd: unversioned },
    { name: "registry entry", cwd: registered },
    { name: "env peer", cwd: remote, env: { OPENVIKING_PEER_ID: "from-env" } },
    { name: "env source", cwd: remote, env: { OPENVIKING_PEER_SOURCE: "cwd" } },
    { name: "workspace peer off", cwd: remote, env: { OPENVIKING_WORKSPACE_PEER: "0" } },
    { name: "actor peer", cwd: remote, file: { url: "http://127.0.0.1:1933", api_key: "k", actor_peer_id: "actor" } },
    { name: "plugin peer", cwd: remote, file: { url: "http://127.0.0.1:1933", api_key: "k", actor_peer_id: "actor", plugin: { "claude-code": { peerId: "plugin-peer" } } } },
    { name: "plugin source", cwd: plain, file: { url: "http://127.0.0.1:1933", plugin: { peerSource: "cwd" } } },
    { name: "pinned file ignores env peer", cwd: remote, env: { OPENVIKING_PEER_ID: "from-env", OPENVIKING_CREDENTIAL_SOURCE: "cli" }, file: { url: "http://127.0.0.1:1933", api_key: "k" } },
  ];
  for (const harness of ["claude-code", "codex"]) {
    for (const scenario of scenarios) {
      ovcli(scenario.file || { url: "http://127.0.0.1:1933", api_key: "k" });
      const env = isolatedEnv(root, scenario.env);
      const cfg = buildPluginConfig(harness, { cwd: scenario.cwd, env });
      const expected = resolveEffectivePeerId({ cfg, cwd: scenario.cwd, env }).peerId;
      const connection = scriptConnection(harness, { env });
      const actual = resolvePeer({ cwd: scenario.cwd, harness: harness === "codex" ? "codex" : "claude_code", connection, env }).peerId;
      assert.equal(actual, expected, `${harness}: ${scenario.name}`);
    }
  }
  // And the default really is the normalized remote.
  ovcli({ url: "http://127.0.0.1:1933", api_key: "k" });
  const env = isolatedEnv(root);
  assert.equal(resolvePeer({ cwd: remote, harness: "claude_code", connection: scriptConnection("claude-code", { env }), env }).peerId, "github.com-volcengine-openviking");
});

test("the connection matches what the plugins resolve", (t) => {
  const root = tempRoot(t);
  mkdirSync(join(root, "home"), { recursive: true });
  const write = (body) => writeFileSync(join(root, "ovcli.conf"), JSON.stringify(body));
  const scenarios = [
    { file: { url: "https://ov.example.com/openviking/", api_key: "file-key" } },
    { file: { url: "https://ov.example.com", api_key: "file-key" }, env: { OPENVIKING_API_KEY: "env-key" } },
    { file: { url: "https://ov.example.com", api_key: "file-key" }, env: { OPENVIKING_URL: "http://other:1933" } },
    { file: { url: "https://ov.example.com", api_key: "file-key", account: "acme", user: "alice" } },
    { file: { url: "https://ov.example.com", api_key: "file-key" }, env: { OPENVIKING_URL: "http://other:1933", OPENVIKING_CREDENTIAL_SOURCE: "cli" } },
    { file: { url: "https://ov.example.com", plugin: { apiKey: "plugin-key", authMode: "api_key" } } },
    { file: null, env: { OPENVIKING_URL: "http://env:1933", OPENVIKING_BEARER_TOKEN: "bearer" } },
    { file: null },
  ];
  for (const scenario of scenarios) {
    if (scenario.file) write(scenario.file);
    else rmSync(join(root, "ovcli.conf"), { force: true });
    const env = isolatedEnv(root, scenario.env);
    const expected = libConnection("claude-code", { env });
    const actual = scriptConnection("claude-code", { env });
    for (const key of ["baseUrl", "apiKey", "account", "user", "peerId", "authMode", "sendIdentityHeaders", "credentialSource"]) {
      assert.deepEqual(actual[key], expected[key], `${JSON.stringify(scenario)}: ${key}`);
    }
  }
});

// ---------------------------------------------------------------------------
// Against a server
// ---------------------------------------------------------------------------

/** Just enough of the sessions API: create, get, batch, commit, tasks. */
async function fakeServer(t, { failAuth = false } = {}) {
  const sessions = new Map();
  const calls = [];
  const server = createServer((req, res) => {
    let body = "";
    req.on("data", (chunk) => { body += chunk; });
    req.on("end", () => {
      const json = body ? JSON.parse(body) : undefined;
      calls.push({ method: req.method, url: req.url, body: json, auth: req.headers.authorization || "" });
      const send = (status, payload) => {
        res.writeHead(status, { "content-type": "application/json" });
        res.end(JSON.stringify(payload));
      };
      if (failAuth) return send(401, { status: "error", error: { code: "UNAUTHENTICATED", message: "bad key" } });
      const ok = (result) => send(200, { status: "ok", result });
      const notFound = () => send(404, { status: "error", error: { code: "NOT_FOUND", message: "not found" } });
      const url = new URL(req.url, "http://x");
      const parts = url.pathname.split("/").filter(Boolean).map(decodeURIComponent);
      if (req.method === "POST" && url.pathname === "/api/v1/sessions") {
        if (sessions.has(json.session_id)) return send(409, { status: "error", error: { code: "ALREADY_EXISTS", message: "exists" } });
        sessions.set(json.session_id, { messages: [], commits: 0, autoCommit: json.auto_commit_policy });
        return ok({ session_id: json.session_id });
      }
      if (parts[2] === "sessions" && parts.length === 4 && req.method === "GET") {
        const s = sessions.get(parts[3]);
        return s ? ok({ session_id: parts[3], message_count: s.messages.length, commit_count: s.commits }) : notFound();
      }
      if (parts[2] === "sessions" && parts[4] === "messages" && parts[5] === "batch") {
        const s = sessions.get(parts[3]) || { messages: [], commits: 0 };
        sessions.set(parts[3], s);
        s.messages.push(...json.messages);
        return ok({ added: json.messages.length });
      }
      if (parts[2] === "sessions" && parts[4] === "commit") {
        const s = sessions.get(parts[3]);
        s.commits += 1;
        s.archived = s.messages.length;
        return ok({ session_id: parts[3], status: "accepted", task_id: `task-${parts[3]}` });
      }
      if (parts[2] === "tasks") return ok({ task_id: parts[3], status: "completed" });
      return notFound();
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => server.close());
  return { url: `http://127.0.0.1:${server.address().port}`, sessions, calls };
}

function fixtureHome(root, url) {
  const home = join(root, "home");
  const repo = join(home, "src", "app");
  gitRepo(repo, "https://github.com/acme/app.git");
  const other = join(home, "src", "other");
  gitRepo(other, "https://github.com/acme/other.git");
  mkdirSync(join(home, ".openviking"), { recursive: true });
  writeFileSync(join(home, ".openviking", "ovcli.conf"), JSON.stringify({ url, api_key: "the-real-key-123" }));
  const projects = join(home, ".claude", "projects");
  const slug = (dir) => dir.replace(/[^A-Za-z0-9]/g, "-");
  const write = (dir, id, start, turns) => writeJsonl(join(projects, slug(dir), `${id}.jsonl`), claudeTranscript(id, dir, start, turns), start + turns.length * 60_000 + 1000);
  write(repo, "s-old", NOW - 30 * DAY, [{ user: "ancient work", assistant: "ok" }]);
  write(repo, "s-1", NOW - 3 * DAY, [{ user: "Add rate limiting to the API, key is --api-key the-real-key-123", tool: "ls", assistant: "Added a token bucket." }]);
  write(join(repo, "src"), "s-2", NOW - 2 * DAY, [{ user: "Why is CI red?", assistant: "A missing env var." }, { user: "Fix it", assistant: "Fixed." }]);
  write(repo, "s-live", NOW - DAY, [{ user: "Already captured by the plugin", assistant: "yes" }]);
  write(repo, "s-active", NOW - 30 * 60_000, [{ user: "this very conversation", assistant: "still running" }]);
  write(repo, "s-util", NOW - DAY, [{ user: "You are a memory relevance compressor utility.\nDo not use any tools.", assistant: "- bullet" }]);
  write(other, "s-other", NOW - DAY, [{ user: "other project work", assistant: "done" }]);
  return { home, repo, other };
}

async function runMain(args, env) {
  let out = "";
  let err = "";
  const code = await main(args, {
    env,
    now: NOW,
    stdout: { write: (s) => { out += s; } },
    stderr: { write: (s) => { err += s; } },
  });
  return { code, out, err };
}

test("dry run reports the plan and writes nothing; --yes imports; a re-run is a no-op", async (t) => {
  const root = tempRoot(t);
  const server = await fakeServer(t);
  const { home, repo } = fixtureHome(root, server.url);
  server.sessions.set("cc-s-live", { messages: [{}], commits: 1 });
  const env = { HOME: home };

  const dry = await runMain(["--harness", "claude", "--cwd", repo, "--json"], env);
  assert.equal(dry.code, 0, dry.err);
  const plan = JSON.parse(dry.out);
  assert.equal(plan.mode, "dry-run");
  assert.equal(plan.projectPeer, "github.com-acme-app");
  assert.deepEqual(plan.sessions.map((s) => [s.id, s.state]), [
    ["import__claude_code__s-live", "live"],
    ["import__claude_code__s-2", "new"],
    ["import__claude_code__s-1", "new"],
  ]);
  assert.deepEqual(plan.toImport.sessions, 2);
  assert.equal(plan.toImport.messages, 6);
  assert.equal(plan.skipped.capturedByPlugin, 1);
  assert.equal(plan.skipped.active, 1);
  assert.equal(plan.skipped.outsideWindow, 1);
  assert.equal(plan.skipped.otherProject, 1);
  assert.equal(plan.skipped.utility, 1);
  assert.ok(server.calls.every((c) => c.method === "GET"), "a dry run only reads");
  assert.ok(server.calls.every((c) => c.auth === "Bearer the-real-key-123"));
  assert.doesNotMatch(dry.out, /the-real-key-123/);

  const text = await runMain(["--harness", "claude", "--cwd", repo], env);
  assert.match(text.out, /dry run, no conversation content was sent/);
  assert.match(text.out, /to import:\s+2 sessions, 6 messages/);
  assert.match(text.out, /Run again with --yes to import/);
  assert.match(text.out, /\b1 other project\b(?!s)/);
  assert.doesNotMatch(text.out, /the-real-key-123/);

  const imported = await runMain(["--harness", "claude", "--cwd", repo, "--yes", "--wait", "--json"], env);
  assert.equal(imported.code, 0, imported.err);
  const result = JSON.parse(imported.out);
  assert.deepEqual(result.results.map((r) => [r.id, r.ok, r.sent, r.extraction]), [
    ["import__claude_code__s-2", true, 4, "completed"],
    ["import__claude_code__s-1", true, 2, "completed"],
  ]);
  const s1 = server.sessions.get("import__claude_code__s-1");
  assert.equal(s1.commits, 1);
  assert.equal(s1.autoCommit, null, "created with auto-commit off");
  assert.deepEqual(s1.messages.map((m) => m.role), ["user", "assistant"]);
  assert.ok(s1.messages.every((m) => m.peer_id === "github.com-acme-app"));
  assert.equal(s1.messages[0].created_at, iso(NOW - 3 * DAY + 60_000));
  assert.match(s1.messages[0].parts[0].text, /--api-key \[REDACTED\]/);
  assert.doesNotMatch(JSON.stringify([...server.sessions.values()]), /the-real-key-123|tool output/);
  assert.equal(server.sessions.get("import__claude_code__s-2").messages[0].peer_id, "github.com-acme-app", "a subdirectory is the same repository");

  const writes = server.calls.length;
  const again = await runMain(["--harness", "claude", "--cwd", repo, "--yes", "--json"], env);
  assert.equal(again.code, 0, again.err);
  const second = JSON.parse(again.out);
  assert.equal(second.results.length, 0);
  assert.equal(second.skipped.alreadyImported, 2);
  assert.ok(server.calls.slice(writes).every((c) => c.method === "GET"), "a re-run writes nothing");
});

test("--list shows each session's id, state and a preview, and only reads", async (t) => {
  const root = tempRoot(t);
  const server = await fakeServer(t);
  const { home, repo } = fixtureHome(root, server.url);
  server.sessions.set("cc-s-live", { messages: [{}], commits: 1 });
  const env = { HOME: home };

  const listed = await runMain(["--harness", "claude", "--cwd", repo, "--list", "--json"], env);
  assert.equal(listed.code, 0, listed.err);
  const list = JSON.parse(listed.out);
  assert.equal(list.mode, "list");
  assert.deepEqual(list.sessions.map((s) => [s.nativeId, s.state]), [["s-live", "live"], ["s-2", "new"], ["s-1", "new"]]);
  const s2 = list.sessions.find((s) => s.nativeId === "s-2");
  assert.equal(s2.firstPrompt, "Why is CI red?");
  assert.equal(s2.lastPrompt, "Fix it");
  const s1 = list.sessions.find((s) => s.nativeId === "s-1");
  assert.match(s1.firstPrompt, /--api-key \[REDACTED\]/, "previews are redacted like the import");
  assert.equal(s1.lastPrompt, null, "one request has no separate last preview");
  assert.equal(s1.title, undefined, "the preview replaces the title in JSON");
  assert.ok(server.calls.every((c) => c.method === "GET"), "listing only reads");

  const text = await runMain(["--harness", "claude", "--cwd", repo, "--list"], env);
  assert.match(text.out, /session list, no conversation content was sent/);
  assert.match(text.out, /listed:\s+3 sessions, 2 not yet in OpenViking/);
  assert.match(text.out, /^ {4}s-2 {2}\S+ \S+Z {2}4 msgs {2}new {2}~\/src\/app\/src$/m);
  assert.match(text.out, /first: "Why is CI red\?"/);
  assert.match(text.out, /last: {2}"Fix it"/);
  assert.match(text.out, /captured by the plugin/);
  assert.match(text.out, /--session <id>/);
  assert.doesNotMatch(text.out, /the-real-key-123/);

  // --list is not held to the import default of 20, and says how to widen it.
  const capped = await runMain(["--harness", "claude", "--cwd", repo, "--list", "--max-sessions", "1"], env);
  assert.match(capped.out, /Not shown: 2 more not listed: raise --max-sessions; 1 older: widen --since \(e\.g\. --since all\); 1 in other projects: add --all-projects\./);
  const projects = join(home, ".claude", "projects");
  for (let i = 0; i < 25; i += 1) {
    const id = `bulk-${String(i).padStart(2, "0")}`;
    writeJsonl(join(projects, repo.replace(/[^A-Za-z0-9]/g, "-"), `${id}.jsonl`), claudeTranscript(id, repo, NOW - 5 * DAY, [{ user: `bulk ${i}`, assistant: "ok" }]), NOW - 5 * DAY + 120_000);
  }
  const wide = JSON.parse((await runMain(["--harness", "claude", "--cwd", repo, "--list", "--json"], env)).out);
  assert.equal(wide.sessions.length, 28);
  assert.equal(wide.skipped.overLimit, 0);
  const dry = JSON.parse((await runMain(["--harness", "claude", "--cwd", repo, "--json"], env)).out);
  assert.equal(dry.sessions.length, 20, "the import itself keeps its default of 20");

  const refused = await runMain(["--harness", "claude", "--list", "--yes"], env);
  assert.equal(refused.code, 2);
  assert.match(refused.err, /--list only lists/);
});

test("--session imports just the picked sessions, whatever their age or project", async (t) => {
  const root = tempRoot(t);
  const server = await fakeServer(t);
  const { home, repo, other } = fixtureHome(root, server.url);
  const env = { HOME: home };
  const projects = join(home, ".claude", "projects");
  const slug = (dir) => dir.replace(/[^A-Za-z0-9]/g, "-");
  for (const [id, words] of [["abcdef12-0000-4000-8000-000000000000", "first uuid"], ["abcdef12-1111-4000-8000-000000000000", "second uuid"]]) {
    writeJsonl(join(projects, slug(other), `${id}.jsonl`), claudeTranscript(id, other, NOW - 5 * DAY, [{ user: words, assistant: "ok" }]), NOW - 5 * DAY + 120_000);
  }

  // From the repo, with the default 14-day window: s-old is 30 days old and
  // s-other is another project, yet both are picked; the import id works too.
  const dry = await runMain(["--harness", "claude", "--cwd", repo, "--session", "import__claude_code__s-old,s-other", "--session", "abcdef12-0", "--json"], env);
  assert.equal(dry.code, 0, dry.err);
  const plan = JSON.parse(dry.out);
  assert.equal(plan.mode, "dry-run");
  assert.deepEqual(plan.picked, ["import__claude_code__s-old", "s-other", "abcdef12-0"]);
  assert.equal(plan.scope, "picked");
  assert.equal(plan.window.since, null, "picked sessions have no time window");
  assert.deepEqual(plan.sessions.map((s) => s.nativeId).sort(), ["abcdef12-0000-4000-8000-000000000000", "s-old", "s-other"]);
  assert.equal(plan.toImport.sessions, 3);
  assert.equal(plan.skipped.outsideWindow + plan.skipped.otherProject + plan.skipped.overLimit, 0);
  assert.equal(plan.sessions.find((s) => s.nativeId === "s-other").peer, "github.com-acme-other", "each keeps its own project's peer");
  assert.ok(server.calls.every((c) => c.method === "GET"));

  const text = await runMain(["--harness", "claude", "--session", "s-other"], env);
  assert.match(text.out, /picked:\s+1 session id \(s-other\)/);
  assert.match(text.out, /window:\s+any age/);

  // A pick that matches nothing, or more than one, refuses the run before anything is sent.
  const calls = server.calls.length;
  const missing = await runMain(["--harness", "claude", "--session", "s-1,nope", "--yes"], env);
  assert.equal(missing.code, 2);
  assert.match(missing.err, /no session matches "nope".*Nothing was imported; run --list/);
  const ambiguous = await runMain(["--harness", "claude", "--session", "abcdef12", "--yes"], env);
  assert.equal(ambiguous.code, 2);
  assert.match(ambiguous.err, /"abcdef12" matches more than one session/);
  const short = await runMain(["--harness", "claude", "--session", "s-", "--yes"], env);
  assert.equal(short.code, 2, "a prefix shorter than 8 characters is not a match");
  assert.equal(server.calls.length, calls, "refused runs never reach the server");

  // An empty session is not importable, and says so.
  writeJsonl(join(projects, slug(other), "s-empty.jsonl"), [{ type: "queue-operation", operation: "enqueue", sessionId: "s-empty", timestamp: iso(NOW - DAY) }], NOW - DAY);
  const empty = await runMain(["--harness", "claude", "--session", "s-empty"], env);
  assert.equal(empty.code, 2);
  assert.match(empty.err, /"s-empty" matches nothing importable/);

  // A picked session that is still active is skipped like any other.
  const active = JSON.parse((await runMain(["--harness", "claude", "--session", "s-active,s-1", "--json"], env)).out);
  assert.deepEqual(active.sessions.map((s) => s.nativeId), ["s-1"]);
  assert.equal(active.skipped.active, 1);

  const imported = await runMain(["--harness", "claude", "--cwd", repo, "--session", "s-old", "--yes", "--json"], env);
  assert.equal(imported.code, 0, imported.err);
  assert.deepEqual(JSON.parse(imported.out).results.map((r) => [r.id, r.ok]), [["import__claude_code__s-old", true]]);
  assert.deepEqual([...server.sessions.keys()].filter((k) => k.startsWith("import__")), ["import__claude_code__s-old"]);
});

test("--session: an exact id wins over a prefix of a longer one (input ids, Codex forks)", async (t) => {
  const root = tempRoot(t);
  const server = await fakeServer(t);
  const home = join(root, "home");
  const repo = join(home, "app");
  gitRepo(repo, "git@github.com:acme/app.git");
  mkdirSync(join(home, ".openviking"), { recursive: true });
  writeFileSync(join(home, ".openviking", "ovcli.conf"), JSON.stringify({ url: server.url, api_key: "k-123456" }));
  const env = { HOME: home };

  const input = join(root, "sessions.jsonl");
  const at = iso(NOW - 3 * DAY);
  writeFileSync(input, ["session-1", "session-10"].map((id) => JSON.stringify({ id, cwd: repo, messages: [{ role: "user", text: `work in ${id}`, created_at: at }, { role: "assistant", text: "ok", created_at: at }] })).join("\n") + "\n");
  const exact = JSON.parse((await runMain(["--harness", "opencode", "--input", input, "--session", "session-1", "--json"], env)).out);
  assert.deepEqual(exact.sessions.map((s) => s.nativeId), ["session-1"]);
  const byPrefix = await runMain(["--harness", "opencode", "--input", input, "--session", "session-", "--json"], env);
  assert.equal(byPrefix.code, 2, "a prefix shared by both is still ambiguous");

  // A fork's rollout carries the parent's uuid and its own: its native id is
  // `<parent>_<fork>`, so the parent's full id is also a prefix of the fork's.
  const parent = "019a0000-1111-7222-8333-444444444444";
  const fork = "019a0000-5555-7666-8777-888888888888";
  const start = NOW - 2 * DAY;
  const dir = join(home, ".codex", "sessions", "2026", "10", "11");
  writeJsonl(join(dir, `rollout-2026-10-11T12-00-00-${parent}.jsonl`), codexRollout(parent, repo, start, [{ user: "parent work", assistant: "ok" }]), start + 10 * 60_000);
  writeJsonl(join(dir, `rollout-2026-10-11T13-00-00-${parent}-${fork}.jsonl`), codexRollout(parent, repo, start, [{ user: "fork work", assistant: "ok" }]), start + 20 * 60_000);
  const listed = JSON.parse((await runMain(["--harness", "codex", "--cwd", repo, "--list", "--json"], env)).out);
  assert.deepEqual(listed.sessions.map((s) => s.nativeId).sort(), [parent, `${parent}_${fork}`]);
  const picked = await runMain(["--harness", "codex", "--session", parent, "--json"], env);
  assert.equal(picked.code, 0, picked.err);
  assert.deepEqual(JSON.parse(picked.out).sessions.map((s) => s.nativeId), [parent]);
  const both = JSON.parse((await runMain(["--harness", "codex", "--session", `${parent},${parent}_${fork}`, "--json"], env)).out);
  assert.equal(both.sessions.length, 2);
});

test("a run that stopped before its commit is finished, not repeated", async (t) => {
  const root = tempRoot(t);
  const server = await fakeServer(t);
  const { home, repo } = fixtureHome(root, server.url);
  // s-2 has 4 turns; an earlier run got the first 2 across and stopped.
  server.sessions.set("import__claude_code__s-2", { messages: [{ role: "user" }, { role: "assistant" }], commits: 0 });

  // The newest two: s-live (not captured on this server) and s-2.
  const run = await runMain(["--harness", "claude", "--cwd", repo, "--yes", "--max-sessions", "2", "--json"], { HOME: home });
  assert.equal(run.code, 0, run.err);
  const s2 = server.sessions.get("import__claude_code__s-2");
  assert.equal(s2.messages.length, 4);
  assert.equal(s2.commits, 1);
  assert.equal(JSON.parse(run.out).skipped.overLimit, 1);
});

test("--all-projects, --peer and --since widen or pin the scope", async (t) => {
  const root = tempRoot(t);
  const server = await fakeServer(t);
  const { home, repo } = fixtureHome(root, server.url);
  const env = { HOME: home };

  const all = JSON.parse((await runMain(["--harness", "claude", "--all-projects", "--since", "60d", "--json"], env)).out);
  assert.equal(all.project, null);
  const byId = Object.fromEntries(all.sessions.map((s) => [s.id, s.peer]));
  assert.equal(byId["import__claude_code__s-other"], "github.com-acme-other");
  assert.equal(byId["import__claude_code__s-old"], "github.com-acme-app");

  const pinned = JSON.parse((await runMain(["--harness", "claude", "--cwd", repo, "--peer", "my-peer", "--json"], env)).out);
  assert.ok(pinned.sessions.every((s) => s.peer === "my-peer"));
});

test("a rejected key stops the import with a hint and nothing written", async (t) => {
  const root = tempRoot(t);
  const server = await fakeServer(t, { failAuth: true });
  const { home, repo } = fixtureHome(root, server.url);

  const run = await runMain(["--harness", "claude", "--cwd", repo, "--yes"], { HOME: home });
  assert.equal(run.code, 1);
  assert.match(run.err, /rejected the API key \(HTTP 401\); check the key in ~\/\.openviking\/ovcli\.conf\. Nothing was imported/);
  assert.ok(server.calls.every((c) => c.method === "GET"));

  // A dry run still reports what it found locally.
  const dry = await runMain(["--harness", "claude", "--cwd", repo], { HOME: home });
  assert.equal(dry.code, 0);
  assert.match(dry.out, /could not check the server/);
});

test("Codex history imports under the codex ids", async (t) => {
  const root = tempRoot(t);
  const server = await fakeServer(t);
  const home = join(root, "home");
  const repo = join(home, "app");
  gitRepo(repo, "git@github.com:acme/app.git");
  mkdirSync(join(home, ".openviking"), { recursive: true });
  writeFileSync(join(home, ".openviking", "ovcli.conf"), JSON.stringify({ url: server.url, api_key: "k-123456" }));
  const id = "019a0000-1111-7222-8333-444444444444";
  const start = NOW - 2 * DAY;
  writeJsonl(join(home, ".codex", "sessions", "2026", "10", "11", `rollout-2026-10-11T12-00-00-${id}.jsonl`),
    codexRollout(id, repo, start, [{ user: "Use bazelisk", assistant: "Will do." }]), start + 10 * 60_000);

  const run = await runMain(["--harness", "codex", "--cwd", repo, "--yes", "--json"], { HOME: home });
  assert.equal(run.code, 0, run.err);
  const session = server.sessions.get(`import__codex__${id}`);
  assert.deepEqual(session.messages.map((m) => [m.role, m.parts[0].text, m.peer_id]), [
    ["user", "Use bazelisk", "github.com-acme-app"],
    ["assistant", "Will do.", "github.com-acme-app"],
  ]);
  assert.ok(server.calls.some((c) => c.url === `/api/v1/sessions/cx-${id}`), "checks for the plugin's own capture");
});

test("other agents get a clear not-supported answer; bad input a usage error", async () => {
  const unsupported = await runMain(["--harness", "cursor"], { HOME: "/nonexistent" });
  assert.equal(unsupported.code, 0);
  assert.match(unsupported.out, /History import for cursor is not supported yet/);

  assert.equal((await runMain([], {})).code, 2);
  assert.equal((await runMain(["--harness", "claude", "--since", "soon"], { HOME: "/nonexistent" })).code, 2);
  assert.equal(parseSince("14d", NOW), NOW - 14 * DAY);
  assert.equal(parseSince("all", NOW), 0);
});

test("runs as a standalone file with node", () => {
  const result = spawnSync(process.execPath, [SCRIPT, "--help"], { encoding: "utf8" });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Usage: node import-history\.mjs --harness claude\|codex/);
  const syntax = spawnSync(process.execPath, ["--check", SCRIPT], { encoding: "utf8" });
  assert.equal(syntax.status, 0, syntax.stderr);
});

// ---------------------------------------------------------------------------
// --input: sessions a converter wrote for an agent the script cannot read
// ---------------------------------------------------------------------------

function inputHome(root, url) {
  const home = join(root, "home");
  const repo = join(home, "src", "app");
  gitRepo(repo, "https://github.com/acme/app.git");
  const other = join(home, "src", "other");
  gitRepo(other, "https://github.com/acme/other.git");
  mkdirSync(join(home, ".openviking"), { recursive: true });
  writeFileSync(join(home, ".openviking", "ovcli.conf"), JSON.stringify({ url, api_key: "the-real-key-123" }));
  return { home, repo, other };
}

function writeInput(path, lines) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${lines.map((l) => (typeof l === "string" ? l : JSON.stringify(l))).join("\n")}\n`);
  return path;
}

/** A user/assistant exchange per pair, a minute apart, ending at `end`. */
function exchange(end, pairs) {
  const out = [];
  pairs.forEach(([user, assistant], i) => {
    const at = end - (pairs.length - i) * 60_000;
    out.push({ role: "user", text: user, created_at: iso(at) }, { role: "assistant", text: assistant, created_at: iso(at + 30_000) });
  });
  return out;
}

test("--input: a malformed file is refused whole, with line numbers, and nothing is sent", async (t) => {
  const root = tempRoot(t);
  const server = await fakeServer(t);
  const { home, repo } = inputHome(root, server.url);
  const good = { id: "ok-1", cwd: repo, messages: exchange(NOW - DAY, [["hi", "hello"]]) };
  const input = writeInput(join(home, "sessions.jsonl"), [
    good,
    "",
    "{not json",
    "[1, 2]",
    { cwd: repo, messages: [] },
    { id: "no-messages", cwd: repo },
    { id: "bad-text", cwd: repo, messages: [{ role: "user", text: { secret: "converter bug" } }] },
    { ...good },
    { id: "relative", cwd: "src/app", messages: [] },
    // Different ids, one session id: they would merge into one server session.
    { id: "x/y", cwd: repo, messages: [] },
    { id: "x:y", cwd: repo, messages: [] },
    { id: "会话一", cwd: repo, messages: [] },
    { id: "tool-object", cwd: repo, messages: [{ role: "tool", text: { k: 1 } }] },
  ]);

  const run = await runMain(["--harness", "opencode", "--input", input, "--cwd", repo, "--yes"], { HOME: home });
  assert.equal(run.code, 1);
  for (const expected of [
    /line 3: not valid JSON/,
    /line 4: not a JSON object/,
    /line 5: "id" must be a non-empty string/,
    /line 6: "messages" must be an array/,
    /line 7: messages\[0\]\.text must be a string/,
    /line 8: repeats the id of line 1/,
    /line 9: "cwd" must be an absolute directory/,
    /line 11: id becomes the same session id as line 10 \(x-y\)/,
    /line 12: "id" has no letter, digit or \._@- a session id can keep/,
    /9 lines cannot be read; nothing was sent/,
  ]) assert.match(run.err, expected);
  assert.doesNotMatch(run.err, /line 1:|line 2:|line 10:|line 13:|converter bug/);
  assert.equal(server.calls.length, 0, "nothing is sent, not even a read");

  // At most ten are listed; --json carries them all.
  const many = writeInput(join(home, "many.jsonl"), Array.from({ length: 14 }, () => "nope"));
  const json = await runMain(["--harness", "pi", "--input", many, "--json"], { HOME: home });
  assert.equal(json.code, 1);
  assert.match(json.err, /line 10: not valid JSON\n {2}\.\.\. and 4 more/);
  assert.doesNotMatch(json.err, /line 11:/);
  assert.equal(JSON.parse(json.out).problems.length, 14);

  const missing = await runMain(["--harness", "pi", "--input", join(home, "absent.jsonl")], { HOME: home });
  assert.equal(missing.code, 1);
  assert.match(missing.err, /cannot read --input .*absent\.jsonl \(ENOENT\)/);
  const missingJson = await runMain(["--harness", "pi", "--input", join(home, "absent.jsonl"), "--json"], { HOME: home });
  assert.match(JSON.parse(missingJson.out).message, /cannot read --input .*absent\.jsonl \(ENOENT\)/);
  assert.equal(server.calls.length, 0);
});

test("--input runs the same pipeline: filters, roles, dates, redaction, dedupe", async (t) => {
  const root = tempRoot(t);
  const server = await fakeServer(t);
  const { home, repo, other } = inputHome(root, server.url);
  server.sessions.set("oc-ses_live", { messages: [{}], commits: 1 });
  const input = writeInput(join(home, "sessions.jsonl"), [
    {
      id: "ses_new",
      cwd: repo,
      messages: [
        { role: "system", text: "system prompt that must not be imported", created_at: iso(NOW - 3 * DAY) },
        ...exchange(NOW - 3 * DAY, [["Add rate limiting, key is --api-key the-real-key-123 and sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWX, bare: the-real-key-123", "Added a token bucket."]]),
        { role: "tool", text: { output: "tool output that must not be imported" } },
        { role: "assistant", text: "<system-reminder>internal note</system-reminder>And tested it.", created_at: iso(NOW - 3 * DAY) },
      ],
    },
    { id: "ses_sub", cwd: join(repo, "src"), messages: exchange(NOW - 2 * DAY, [["Why is CI red?", "A missing env var."], ["Fix it", "Fixed."]]) },
    { id: "ses_updated", cwd: repo, updated_at: iso(NOW - 4 * DAY), messages: [{ role: "user", text: "no per-message time" }, { role: "assistant", text: "ok" }] },
    { id: "ses_undated", cwd: repo, messages: [{ role: "user", text: "when was this?", created_at: "1" }, { role: "assistant", text: "nobody knows", created_at: "yesterday-ish" }] },
    { id: "ses_child", cwd: repo, subagent: true, messages: exchange(NOW - DAY, [["sub-agent prompt", "sub-agent answer"]]) },
    { id: "ses_live", cwd: repo, messages: exchange(NOW - DAY, [["Already captured by the plugin", "yes"]]) },
    { id: "ses_active", cwd: repo, messages: exchange(NOW - 10 * 60_000, [["this very conversation", "still running"]]) },
    { id: "ses_old", cwd: repo, messages: exchange(NOW - 30 * DAY, [["ancient work", "ok"]]) },
    { id: "ses_other", cwd: other, messages: exchange(NOW - DAY, [["other project work", "done"]]) },
    { id: "ses_nocwd", messages: exchange(NOW - DAY, [["somewhere", "anywhere"]]) },
    { id: "ses_empty", cwd: repo, messages: [{ role: "tool", text: "x" }] },
  ]);
  const env = { HOME: home };
  const args = ["--harness", "opencode", "--input", input, "--cwd", repo];

  const dry = await runMain([...args, "--json"], env);
  assert.equal(dry.code, 0, dry.err);
  const plan = JSON.parse(dry.out);
  assert.equal(plan.mode, "dry-run");
  assert.equal(plan.harness, "opencode");
  assert.equal(plan.historyDir, input);
  assert.equal(plan.projectPeer, "github.com-acme-app");
  assert.deepEqual(plan.sessions.map((s) => [s.id, s.state]), [
    ["import__opencode__ses_live", "live"],
    ["import__opencode__ses_sub", "new"],
    ["import__opencode__ses_new", "new"],
    ["import__opencode__ses_updated", "new"],
  ]);
  assert.equal(plan.sessions.find((s) => s.id.endsWith("ses_updated")).lastActivity, iso(NOW - 4 * DAY));
  assert.deepEqual(plan.skipped, {
    outsideWindow: 1, otherProject: 2, active: 1, empty: 1, utility: 1, overLimit: 0, undated: 1, alreadyImported: 0, capturedByPlugin: 1,
  });
  assert.equal(plan.droppedMessages, 3);
  assert.ok(server.calls.every((c) => c.method === "GET"), "a dry run only reads");
  assert.ok(server.calls.some((c) => c.url === "/api/v1/sessions/oc-ses_new"), "checks for the plugin's own capture");

  const text = await runMain(args, env);
  assert.match(text.out, /harness: {4}OpenCode \(~\/sessions\.jsonl\)/);
  assert.match(text.out, /1 undated \(no time on any message\)/);
  assert.match(text.out, /dropped: {4}3 messages in the input with a role other than user or assistant/);
  assert.doesNotMatch(text.out, /the-real-key-123|system prompt|tool output|sub-agent/);

  const imported = await runMain([...args, "--yes", "--json"], env);
  assert.equal(imported.code, 0, imported.err);
  assert.deepEqual(JSON.parse(imported.out).results.map((r) => [r.id, r.ok]), [
    ["import__opencode__ses_sub", true],
    ["import__opencode__ses_new", true],
    ["import__opencode__ses_updated", true],
  ]);
  const created = server.sessions.get("import__opencode__ses_new");
  assert.equal(created.commits, 1);
  assert.equal(created.autoCommit, null);
  assert.deepEqual(created.messages.map((m) => m.role), ["user", "assistant"]);
  // The bare key is caught only because it is the connection's key.
  assert.match(created.messages[0].parts[0].text, /--api-key \[REDACTED\] and \[REDACTED\], bare: \[REDACTED\]/);
  assert.equal(created.messages[1].parts[0].text, "Added a token bucket.\n\nAnd tested it.");
  assert.equal(created.messages[0].created_at, iso(NOW - 3 * DAY - 60_000));
  assert.ok(created.messages.every((m) => m.peer_id === "github.com-acme-app"));
  assert.equal(server.sessions.get("import__opencode__ses_updated").messages[0].created_at, undefined);
  assert.doesNotMatch(JSON.stringify([...server.sessions.values()]), /the-real-key-123|ABCDEFGHIJKLMNOP|system prompt|tool output|internal note|sub-agent/);

  const writes = server.calls.length;
  const again = await runMain([...args, "--yes", "--json"], env);
  assert.equal(again.code, 0, again.err);
  const second = JSON.parse(again.out);
  assert.equal(second.results.length, 0);
  assert.equal(second.skipped.alreadyImported, 3);
  assert.ok(server.calls.slice(writes).every((c) => c.method === "GET"), "a re-run writes nothing");

  // Every project and any age: the other repository, the one without a cwd and the old one too.
  const all = JSON.parse((await runMain(["--harness", "opencode", "--input", input, "--all-projects", "--since", "60d", "--json"], env)).out);
  const byId = Object.fromEntries(all.sessions.map((s) => [s.id, s.peer]));
  assert.equal(byId["import__opencode__ses_other"], "github.com-acme-other");
  assert.equal(byId["import__opencode__ses_nocwd"], null);
  assert.equal(byId["import__opencode__ses_old"], "github.com-acme-app");
  // --since narrows it.
  const recent = JSON.parse((await runMain([...args, "--since", "36h", "--json"], env)).out);
  assert.deepEqual(recent.sessions.map((s) => s.id), ["import__opencode__ses_live"]);
  assert.equal(recent.skipped.outsideWindow, 4);
});

test("--input: session ids, live ids and peers follow each host's plugin", async (t) => {
  const root = tempRoot(t);
  mkdirSync(join(root, "home"), { recursive: true });
  const repo = join(root, "repo");
  gitRepo(repo, "git@github.com:Volcengine/OpenViking.git");
  const plain = join(root, "plain");
  mkdirSync(plain);

  // Live ids, against the plugins' own derivations.
  const ids = ["ses_4f2a9c", "sess_01J9ZX", "0199AAAA-bbbb-7ccc-8ddd-eeeeeeeeeeee", "a:b/c d?.", "im?a"];
  for (const id of ids) {
    for (const [name, host] of Object.entries(HOOK_HOSTS)) {
      const key = name.replace(/-/g, "_");
      assert.equal(liveSessionId(key, id), deriveAgentSessionId(host.prefix, { session_id: id }), `${name} ${id}`);
    }
    assert.equal(liveSessionId("opencode", id), deriveHarnessSessionId("oc-", id), `opencode ${id}`);
    assert.equal(liveSessionId("pi", id), deriveHarnessSessionId("pi-", id), `pi ${id}`);
    assert.equal(liveSessionId("dsh", id), deriveDshSessionId(id), `dsh ${id}`);
    assert.equal(liveSessionId("trae_cli", id), deriveCodexSessionId(id), `trae-cli ${id}`);
    assert.equal(liveSessionId("codex", id), deriveCodexSessionId(id), `codex ${id}`);
  }
  assert.equal(liveSessionId("openclaw", "0199AAAA-bbbb-7ccc-8ddd-eeeeeeeeeeee"), "0199aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee");
  assert.equal(liveSessionId("openclaw", "a:b"), createHash("sha256").update("openclaw-session:a:b").digest("hex"));
  assert.equal(liveSessionId("hermes", "20261001_abc"), "20261001_abc");
  // Session ids sanitize the way `openviking-server ingest` does.
  assert.equal(sessionId("opencode", "a:b/c d?."), "import__opencode__a-b-c-d");

  // Peers, against the plugin loader with each host's harness name.
  const ovcli = (body) => writeFileSync(join(root, "ovcli.conf"), JSON.stringify(body));
  const hosts = [["cursor", "cursor"], ["trae", "trae"], ["trae-cn", "trae_cn"], ["trae-cli", "trae_cli", "codex"], ["zcode", "zcode"],
    ["kimicode", "kimicode"], ["codebuddy", "codebuddy"], ["opencode", "opencode"], ["pi", "pi"], ["dsh", "dsh"]];
  for (const [name, key, pluginName = name] of hosts) {
    const scenarios = [
      { cwd: repo },
      { cwd: plain },
      { cwd: repo, file: { url: "http://127.0.0.1:1933", api_key: "k", plugin: { peerSource: "{harness}-{dir}" } } },
      { cwd: repo, file: { url: "http://127.0.0.1:1933", api_key: "k", plugin: { [pluginName]: { peerId: `${key}-peer` } } } },
      { cwd: repo, file: { url: "http://127.0.0.1:1933", api_key: "k", plugin: { [pluginName.replace(/-/g, "_")]: { peerId: "snake-peer" } } } },
    ];
    for (const scenario of scenarios) {
      ovcli(scenario.file || { url: "http://127.0.0.1:1933", api_key: "k" });
      const env = isolatedEnv(root);
      const cfg = buildPluginConfig(pluginName, { cwd: scenario.cwd, env });
      const expected = resolveEffectivePeerId({ cfg, cwd: scenario.cwd, env }).peerId;
      const actual = resolvePeer({ cwd: scenario.cwd, harness: key, connection: scriptConnection(pluginName, { env }), env }).peerId;
      assert.equal(actual, expected, `${name}: ${JSON.stringify(scenario)}`);
    }
  }
  ovcli({ url: "http://127.0.0.1:1933", api_key: "k", plugin: { peerSource: "{harness}-{dir}" } });
  const env = isolatedEnv(root);
  assert.equal(resolvePeer({ cwd: repo, harness: "trae_cn", connection: scriptConnection("trae-cn", { env }), env }).peerId, "trae-cn-repo");
});

test("--input end to end for opencode, pi and kimicode: ids, the plugin's capture, the peer", async (t) => {
  const root = tempRoot(t);
  const server = await fakeServer(t);
  const { home, repo } = inputHome(root, server.url);
  const cases = [
    ["opencode", "ses_4f2a9c", "oc-ses_4f2a9c"],
    ["pi", "0199aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee", "pi-0199aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"],
    ["kimicode", "k1:sess 7", "kc-k1-sess-7"],
  ];
  for (const [harness, id, live] of cases) {
    const input = writeInput(join(home, `${harness}.jsonl`), [
      { id, cwd: repo, messages: exchange(NOW - 2 * DAY, [["remember the deploy steps", "Noted."]]) },
      { id: `${id}-captured`, cwd: repo, messages: exchange(NOW - DAY, [["captured live", "yes"]]) },
    ]);
    server.sessions.set(`${live}-captured`, { messages: [{}], commits: 1 });
    const run = await runMain(["--harness", harness, "--input", input, "--cwd", repo, "--yes", "--json"], { HOME: home });
    assert.equal(run.code, 0, run.err);
    const result = JSON.parse(run.out);
    const own = sessionId(harness, id);
    assert.deepEqual(result.sessions.map((s) => [s.id, s.state]), [[sessionId(harness, `${id}-captured`), "live"], [own, "new"]], harness);
    assert.ok(server.calls.some((c) => c.url === `/api/v1/sessions/${encodeURIComponent(live)}`), `${harness}: checks ${live}`);
    assert.deepEqual(server.sessions.get(own).messages.map((m) => [m.role, m.peer_id]), [["user", "github.com-acme-app"], ["assistant", "github.com-acme-app"]]);
  }
  assert.ok(server.sessions.has("import__kimicode__k1-sess-7"));
});

test("--input for OpenClaw and Hermes derives no peer; other hosts point at --input", async (t) => {
  const root = tempRoot(t);
  const server = await fakeServer(t);
  const { home, repo } = inputHome(root, server.url);
  const input = writeInput(join(home, "claw.jsonl"), [{ id: "0199AAAA-bbbb-7ccc-8ddd-eeeeeeeeeeee", cwd: repo, messages: exchange(NOW - DAY, [["hi", "hello"]]) }]);
  const dry = await runMain(["--harness", "openclaw", "--input", input, "--cwd", repo, "--json"], { HOME: home });
  assert.equal(dry.code, 0, dry.err);
  const plan = JSON.parse(dry.out);
  assert.equal(plan.projectPeer, null);
  assert.match(plan.peerNote, /OpenClaw names its peers after the agent/);
  assert.deepEqual(plan.sessions.map((s) => [s.id, s.peer]), [["import__openclaw__0199AAAA-bbbb-7ccc-8ddd-eeeeeeeeeeee", null]]);
  assert.ok(server.calls.some((c) => c.url === "/api/v1/sessions/0199aaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee"));
  const text = await runMain(["--harness", "hermes", "--input", input, "--cwd", repo], { HOME: home });
  assert.match(text.out, /peer: {7}Hermes names its peers after the agent/);

  for (const harness of ["kimicode", "trae-cn", "openclaw"]) {
    const run = await runMain(["--harness", harness], { HOME: "/nonexistent" });
    assert.equal(run.code, 0);
    assert.match(run.out, new RegExp(`History import for ${harness} is not supported yet\\..*\\n.*--input <file\\.jsonl>.*see --help`));
  }
  assert.equal((await runMain(["--harness", "copilot", "--input", input], { HOME: home })).code, 2);
  for (const name of ["constructor", "__proto__", "toString"]) {
    const run = await runMain(["--harness", name, "--input", input], { HOME: home });
    assert.equal(run.code, 2);
    assert.match(run.err, /unknown harness/);
  }
  assert.equal((await runMain(["--harness", "pi", "--input", input, "--history-dir", home], { HOME: home })).code, 2);

  const help = spawnSync(process.execPath, [SCRIPT, "--help"], { encoding: "utf8" });
  assert.match(help.stdout, /--input <file\.jsonl>/);
  assert.match(help.stdout, /Input file \(--input\): one session per line/);
  // Every option's description starts in the same column.
  const options = help.stdout.split("\n").filter((l) => /^ {2}-/.test(l));
  assert.ok(options.length > 10);
  for (const line of options) assert.match(line, /^ {2}\S.{22} \S/, line);
});
