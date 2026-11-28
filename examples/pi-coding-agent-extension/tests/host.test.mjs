/** pi's host for the shared flow: what its payloads read as, the turns a branch decodes to, and tool calls. */

import test from "node:test";
import assert from "node:assert/strict";

import pi from "../host.mjs";
import { captureCount } from "../decode.mjs";
import { turnMessages } from "../shared/flow/capture.mjs";
import { toolCall } from "../shared/flow/index.mjs";

const CFG = { captureAssistantTurns: true, captureToolMaxChars: 2000, captureMaxLength: 24000 };

const message = (id, msg) => ({ type: "message", id, message: msg });
const BRANCH = [
  { type: "session", id: "s0" },
  message("u1", { role: "user", content: "fix the build", timestamp: 1 }),
  message("a1", {
    role: "assistant",
    content: [
      { type: "text", text: "Running it." },
      { type: "toolCall", id: "call-1", name: "bash", arguments: { command: "make" } },
    ],
  }),
  message("t1", { role: "toolResult", toolCallId: "call-1", toolName: "bash", content: [{ type: "text", text: "ok" }] }),
  { type: "custom", id: "c1", customType: "ov-takeover", data: {} },
  message("a2", { role: "assistant", content: [{ type: "text", text: "Fixed." }] }),
];

const decodeBranch = (branch, cursor = {}) => {
  const decoded = pi.decode({ text: "", cursor, notes: [], payload: { branch }, cfg: CFG });
  return { decoded, messages: turnMessages(decoded, CFG).filter(Boolean) };
};

test("each message entry is one turn keyed by its entry id; other entries are skipped", () => {
  const { decoded, messages } = decodeBranch(BRANCH);
  assert.deepEqual(decoded.turns.map((turn) => turn.turnId), ["u1", "a1", "t1", "a2"]);
  assert.deepEqual(messages.map((m) => [m.role, m.turn_id]), [["user", "u1"], ["assistant", "a1"], ["user", "t1"], ["assistant", "a2"]]);
  assert.deepEqual(messages[0].parts, [{ type: "text", text: "fix the build" }]);
  assert.ok(messages[1].parts.some((part) => part.type === "tool"), JSON.stringify(messages[1].parts));
  assert.equal(captureCount(BRANCH, CFG), 4);
  assert.equal(captureCount(BRANCH.slice(4), CFG), 1);
});

test("a stored cursor sends only what came after it, also on a sibling branch", () => {
  const first = decodeBranch(BRANCH.slice(0, 3));
  const cursor = first.decoded.cursorAfter(first.decoded.turns.length);
  assert.deepEqual(decodeBranch(BRANCH, cursor).decoded.turns.map((turn) => turn.turnId), ["t1", "a2"]);
  const sibling = [...BRANCH.slice(0, 3), message("b1", { role: "user", content: "other way" })];
  assert.deepEqual(decodeBranch(sibling, decodeBranch(BRANCH).decoded.cursorAfter(4)).decoded.turns.map((t) => t.turnId), ["b1"]);
});

test("the cursor 0.4.x left on the branch is honoured, so an upgraded session is not sent again", () => {
  const branch = [...BRANCH, { type: "custom", id: "c2", customType: "ov-capture", data: { sessionId: "pi-abc", lastEntryId: "t1" } }];
  const read = pi.read("turn_end", { sessionId: "abc", branch });
  assert.deepEqual(pi.legacyCursor("abc", read), { lastEntryId: "t1" });
  assert.deepEqual(decodeBranch(branch, read.legacyCursor).decoded.turns.map((turn) => turn.turnId), ["a2"]);
  // Another session's checkpoint (a fork's parent) does not count.
  assert.equal(pi.read("turn_end", { sessionId: "other", branch, forked: true }).legacyCursor, undefined);
  // A published takeover checkpoint counts on an unforked branch.
  const takeover = [...BRANCH.slice(0, 3), { type: "custom", id: "k", customType: "ov-takeover", data: { syncedEntryCount: 2 } }];
  assert.deepEqual(pi.read("turn_end", { sessionId: "abc", branch: takeover }).legacyCursor, { lastEntryId: "u1" });
  assert.equal(pi.read("turn_end", { sessionId: "abc", branch: takeover, forked: true }).legacyCursor, undefined);
});

test("read: the OpenViking session id, and what a start says happened to the context", () => {
  assert.equal(pi.read("context", { sessionId: "abc", prompt: " hi " }).session, "pi-abc");
  assert.equal(pi.read("context", { sessionId: "abc", prompt: " hi " }).prompt, "hi");
  const start = (payload) => pi.read("session_start", { sessionId: "abc", ...payload }).resumed;
  assert.equal(start({ branch: [] }), "");
  assert.equal(start({ branch: BRANCH }), "resume");
  assert.equal(start({ branch: BRANCH, forked: true }), "");
  assert.equal(start({ branch: BRANCH, reason: "reload" }), "");
  assert.equal(start({ branch: [...BRANCH, { type: "compaction", id: "x" }], reason: "reload" }), "compact");
  assert.equal(pi.read("session_compact", { sessionId: "abc" }).resumed, "compact");
});

test("tool calls: viking:// file access is denied, edit text is not a location, session_history gets this session", () => {
  assert.match(toolCall(pi, { toolName: "read", input: { path: "viking://user/x" } }).deny, /openviking_read/);
  assert.deepEqual(toolCall(pi, { toolName: "edit", input: { path: "a.md", edits: [{ oldText: "a", newText: "viking://x" }] } }), {});
  assert.deepEqual(
    toolCall(pi, { toolName: "openviking_session_history", input: { action: "overview" }, sessionId: "abc" }),
    { input: { action: "overview", session: "pi-abc" } },
  );
  assert.deepEqual(toolCall(pi, { toolName: "openviking_session_history", input: { session: "pi-x" }, sessionId: "abc" }), {});
});
