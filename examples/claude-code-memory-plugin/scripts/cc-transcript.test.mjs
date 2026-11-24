import assert from "node:assert/strict";
import { mkdtemp, open, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { extractCaptureTurns, parseTranscript, readCaptureTurns } from "./cc-transcript.mjs";

const user = (content) => JSON.stringify({ type: "user", message: { role: "user", content } });
const assistant = (content) => JSON.stringify({ type: "assistant", message: { role: "assistant", content } });
const call = (id, name) => assistant([{ type: "tool_use", id, name, input: { command: `run ${id}` } }]);
const result = (id, text) => user([{ type: "tool_result", tool_use_id: id, content: [{ type: "text", text }] }]);

// Multi-byte text throughout: a byte offset and a character offset must not be
// interchangeable anywhere in the reader.
const LINES = [
  JSON.stringify({ type: "summary", summary: "earlier work" }),
  user("记住端口 1933 — remember the port 🚢"),
  assistant([{ type: "thinking", thinking: "…" }, { type: "text", text: "先看一下配置。" }]),
  call("toolu_a", "Read"),
  call("toolu_b", "Bash"),
  result("toolu_a", "port: 1933 ✓"),
  "",
  result("toolu_b", "服务已启动"),
  JSON.stringify({ type: "api-request-blob", message: { role: "user", content: "a copy, not a turn" } }),
  assistant([{ type: "text", text: "端口是 1933。" }]),
  "{ not json",
  assistant([]),
  user("第二个问题：超时是多少？"),
  call("toolu_c", "Grep"),
  result("toolu_c", "timeout: 30s"),
  assistant([{ type: "text", text: "超时为 30 秒。" }]),
  user("谢谢"),
  assistant("不客气。"),
];
const FULL = Buffer.from(`${LINES.join("\n")}\n`, "utf-8");

function wholeFileTurns(bytes) {
  return extractCaptureTurns(parseTranscript(bytes.toString("utf-8")));
}

async function withTranscript(fn) {
  const root = await mkdtemp(join(tmpdir(), "ov-cc-transcript-"));
  try {
    await fn(join(root, "transcript.jsonl"));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
}

// What auto-capture does with a scan: the turns past its cursor.
function newTurns(scan, capturedTurnCount) {
  return scan.turns.slice(Math.max(0, capturedTurnCount - scan.firstIndex));
}

async function assertResumesLikeAWholeRead(path, firstBytes) {
  await writeFile(path, firstBytes);
  const first = await readCaptureTurns(path);
  const firstWhole = wholeFileTurns(firstBytes);
  assert.equal(first.totalTurns, firstWhole.length);
  assert.deepEqual(first.turns, firstWhole);

  await writeFile(path, FULL);
  const captured = first.totalTurns;
  const second = await readCaptureTurns(path, {}, { capturedTurnCount: captured, cursor: first.cursor });
  const whole = wholeFileTurns(FULL);
  assert.equal(second.totalTurns, whole.length);
  assert.ok(second.firstIndex <= captured);
  assert.deepEqual(second.turns, whole.slice(second.firstIndex));
  assert.deepEqual(newTurns(second, captured), whole.slice(captured));
  if (first.cursor) assert.equal(second.readFrom, first.cursor.offset);
  // The final-answer check reads back to the last user turn; it must be kept.
  const lastUser = whole.findLastIndex((turn) => turn.role === "user");
  assert.ok(second.firstIndex <= lastUser);
}

test("resuming from a cursor yields the turns a whole-file read would, wherever the last read stopped", async () => {
  await withTranscript(async (path) => {
    let offset = 0;
    for (const line of LINES) {
      offset += Buffer.byteLength(line, "utf-8") + 1;
      await assertResumesLikeAWholeRead(path, FULL.subarray(0, offset));
    }
  });
});

test("a read that stopped mid-line resumes without losing or repeating a turn", async () => {
  await withTranscript(async (path) => {
    for (let offset = 1; offset < FULL.length; offset += 37) {
      await assertResumesLikeAWholeRead(path, FULL.subarray(0, offset));
    }
  });
});

test("cursors chain across several reads of a growing transcript", async () => {
  await withTranscript(async (path) => {
    const whole = wholeFileTurns(FULL);
    const sent = [];
    let captured = 0;
    let cursor = null;
    let offset = 0;
    for (const line of LINES) {
      offset += Buffer.byteLength(line, "utf-8") + 1;
      await writeFile(path, FULL.subarray(0, offset));
      const scan = await readCaptureTurns(path, {}, { capturedTurnCount: captured, cursor });
      sent.push(...newTurns(scan, captured));
      captured = scan.totalTurns;
      cursor = scan.cursor;
    }
    assert.deepEqual(sent, whole);
    assert.equal(cursor.offset, FULL.indexOf(LINES.at(-2)));
  });
});

test("nothing before the cursor is read", async () => {
  await withTranscript(async (path) => {
    const cut = FULL.indexOf(LINES[12]);
    await writeFile(path, FULL.subarray(0, cut + Buffer.byteLength(LINES[12]) + 1));
    const first = await readCaptureTurns(path);
    assert.equal(first.cursor.offset, cut);

    // Were the head still parsed, these bytes would change the turn count.
    const spoiled = Buffer.from(FULL);
    spoiled.fill("x", 0, cut - 1);
    await writeFile(path, spoiled);
    const second = await readCaptureTurns(path, {}, { capturedTurnCount: first.totalTurns, cursor: first.cursor });
    const whole = wholeFileTurns(FULL);
    assert.equal(second.totalTurns, whole.length);
    assert.deepEqual(newTurns(second, first.totalTurns), whole.slice(first.totalTurns));
  });
});

test("a cursor that no longer fits the file falls back to reading it all", async () => {
  await withTranscript(async (path) => {
    await writeFile(path, FULL);
    const { cursor, totalTurns } = await readCaptureTurns(path);
    const whole = wholeFileTurns(FULL);
    const fullRead = async (options) => {
      const scan = await readCaptureTurns(path, {}, options);
      assert.equal(scan.readFrom, 0);
      assert.equal(scan.totalTurns, whole.length);
      return scan;
    };

    // The capture cursor was rewound behind it.
    const rewound = await fullRead({ capturedTurnCount: 0, cursor });
    assert.deepEqual(rewound.turns, whole);
    // Not a cursor at all.
    await fullRead({ capturedTurnCount: totalTurns, cursor: { offset: "12", turnCount: 1 } });
    await fullRead({ capturedTurnCount: totalTurns, cursor: { ...cursor, offset: cursor.offset + 1 } });
    await fullRead({ capturedTurnCount: totalTurns, cursor: { ...cursor, offset: FULL.length + 10 } });

    // Another transcript under the same path: the offset lands somewhere else.
    const other = Buffer.from(`${[user("另一个会话".repeat(40)), ...LINES].join("\n")}\n`, "utf-8");
    await writeFile(path, other);
    const replaced = await readCaptureTurns(path, {}, { capturedTurnCount: totalTurns, cursor });
    assert.equal(replaced.readFrom, 0);
    assert.equal(replaced.totalTurns, wholeFileTurns(other).length);
  });
});

test("a call made before the cursor still names its result after it", async () => {
  await withTranscript(async (path) => {
    // Stopped between the two results of a parallel call.
    const cut = FULL.indexOf(LINES[7]);
    await writeFile(path, FULL.subarray(0, cut));
    const first = await readCaptureTurns(path);
    assert.deepEqual(first.cursor.toolNames, { toolu_a: "Read", toolu_b: "Bash" });

    await writeFile(path, FULL);
    const second = await readCaptureTurns(path, {}, { capturedTurnCount: first.totalTurns, cursor: first.cursor });
    const [late] = newTurns(second, first.totalTurns);
    assert.equal(late.parts[0].tool_id, "toolu_b");
    assert.equal(late.parts[0].tool_name, "Bash");
    // Both results are in: nothing left to carry.
    assert.equal(second.cursor.toolNames, undefined);
  });
});

test("the single-array transcript form is read whole, without a cursor", async () => {
  await withTranscript(async (path) => {
    const messages = LINES.filter((line) => line.startsWith("{\"")).map((line) => JSON.parse(line));
    const bytes = Buffer.from(JSON.stringify(messages, null, 1), "utf-8");
    await writeFile(path, bytes);
    const scan = await readCaptureTurns(path, {}, { capturedTurnCount: 3 });
    assert.deepEqual(scan.turns, wholeFileTurns(bytes));
    assert.equal(scan.totalTurns, scan.turns.length);
    assert.equal(scan.cursor, null);
  });
});

test("an empty transcript has no turns", async () => {
  await withTranscript(async (path) => {
    await writeFile(path, "");
    assert.equal((await readCaptureTurns(path)).totalTurns, 0);
    await writeFile(path, "\n\n");
    assert.equal((await readCaptureTurns(path)).totalTurns, 0);
  });
});

test("lines longer than one read chunk keep their offsets", async () => {
  await withTranscript(async (path) => {
    // Three chunks' worth in one line, then a turn whose offset depends on it.
    const big = user(`${"长".repeat(1_100_000)} end`);
    const lines = [user("before"), big, assistant("after the long line"), user("last")];
    const bytes = Buffer.from(`${lines.join("\n")}\n`, "utf-8");
    await writeFile(path, bytes);
    const scan = await readCaptureTurns(path);
    assert.deepEqual(scan.turns, wholeFileTurns(bytes));
    assert.equal(scan.cursor.offset, bytes.lastIndexOf(lines[3]));

    const handle = await open(path, "a");
    await handle.write(`${assistant("appended")}\n`);
    await handle.close();
    const next = await readCaptureTurns(path, {}, { capturedTurnCount: scan.totalTurns, cursor: scan.cursor });
    assert.deepEqual(newTurns(next, scan.totalTurns).map((turn) => turn.text), ["appended"]);
  });
});
