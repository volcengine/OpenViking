/**
 * The wire reader: complete lines, byte cursors, file identity, protocol gate.
 */

import assert from "node:assert/strict";
import { appendFile, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  compareProtocolVersions,
  emptyReaderState,
  readWireRecords,
} from "../../scripts/wire-reader.mjs";
import { contentPart, metadata, stepBegin, turnEnded, turnPrompt, wireText } from "../fixtures/wire-fixtures.mjs";

async function withWire(fn) {
  const dir = await mkdtemp(join(tmpdir(), "kc-wire-"));
  const file = join(dir, "wire.jsonl");
  try {
    return await fn(file, dir);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

test("only newline-terminated lines are consumed, and the cursor resumes mid-file", async () => {
  await withWire(async (file) => {
    const complete = wireText([metadata(), turnPrompt({ turnId: 0, text: "first" })]);
    await writeFile(file, `${complete}${JSON.stringify(turnEnded({ turnId: 0 }))}`, "utf8");

    const first = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(first.status, "ok");
    // The metadata and the prompt are complete; the unterminated ended record is not.
    assert.deepEqual(first.records.map((item) => item.record.type), ["metadata", "turn.prompt"]);
    assert.equal(first.state.line, 2);

    // Finish the line the host was writing when we read.
    await appendFile(file, "\n", "utf8");
    const second = await readWireRecords(file, first.state);
    assert.equal(second.status, "ok");
    assert.deepEqual(second.records.map((item) => item.record.type), ["turn.ended"]);
    assert.equal(second.state.line, 3);
    assert.equal(second.state.scanOffset, first.state.scanOffset + Buffer.byteLength("\n", "utf8") + Buffer.byteLength(JSON.stringify(turnEnded({ turnId: 0 })), "utf8"));
  });
});

test("a file with no metadata line is legacy and is not parsed as 1.5", async () => {
  await withWire(async (file) => {
    await writeFile(file, wireText([turnPrompt({ turnId: 0 })]), "utf8");
    const result = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(result.status, "legacy");
    assert.deepEqual(result.records, []);
    assert.equal(result.state.legacy, true);
  });
});

test("a newer min_protocol_version stops the reader and keeps its cursor", async () => {
  await withWire(async (file) => {
    await writeFile(file, wireText([metadata({ protocol_version: "2.0", min_protocol_version: "2.0" })]), "utf8");
    const result = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(result.status, "incompatible");
    assert.equal(result.minProtocolVersion, "2.0");
    assert.equal(result.state.incompatible, true);
    // Nothing was consumed: the cursor is still where it was.
    assert.equal(result.state.scanOffset, 0);

    // A second call must not start parsing the file either.
    await appendFile(file, wireText([turnPrompt({ turnId: 1 })], { trailingNewline: true }), "utf8");
    const again = await readWireRecords(file, result.state);
    assert.equal(again.status, "incompatible");
    assert.equal(again.state.scanOffset, 0);
  });
});

test("protocol versions compare numerically, not lexically", () => {
  assert.equal(compareProtocolVersions("1.5", "1.5"), 0);
  assert.equal(compareProtocolVersions("1.10", "1.9"), 1);
  assert.equal(compareProtocolVersions("1.4", "1.5"), -1);
  assert.equal(compareProtocolVersions("2", "1.99"), 1);
});

test("a replaced file resets the cursor instead of resuming into new content", async () => {
  await withWire(async (file, dir) => {
    await writeFile(file, wireText([metadata(), ...Array.from({ length: 3 }, (_, i) => turnPrompt({ turnId: i, text: `q${i}` }))]), "utf8");
    const first = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(first.records.length, 4);

    // Replace the file wholesale with different content of a different size.
    await writeFile(file, wireText([metadata(), turnPrompt({ turnId: 9, text: "different session" })]), "utf8");
    const second = await readWireRecords(file, first.state);
    assert.equal(second.status, "ok");
    assert.equal(second.reset, true);
    assert.deepEqual(second.records.map((item) => item.record.type), ["metadata", "turn.prompt"]);
    assert.equal(second.records[1].record.turnId, 9);
    assert.ok(dir);
  });
});

test("a truncated file re-reads from the beginning", async () => {
  await withWire(async (file) => {
    const many = [metadata(), ...Array.from({ length: 5 }, (_, i) => turnPrompt({ turnId: i, text: `question ${i}` }))];
    await writeFile(file, wireText(many), "utf8");
    const first = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(first.state.line, 6);

    await writeFile(file, wireText([metadata(), turnPrompt({ turnId: 0, text: "only" })]), "utf8");
    const second = await readWireRecords(file, first.state);
    assert.equal(second.status, "ok");
    assert.equal(second.reset, true);
    assert.equal(second.state.line, 2);
  });
});

test("a scan stops at its byte budget and resumes at the same line next time", async () => {
  await withWire(async (file) => {
    const records = [metadata()];
    for (let i = 0; i < 40; i += 1) records.push(turnPrompt({ turnId: i, text: `question number ${i} with padding` }));
    await writeFile(file, wireText(records), "utf8");

    const partial = await readWireRecords(file, emptyReaderState({ wirePath: file }), { limitBytes: 900 });
    assert.equal(partial.status, "ok");
    assert.equal(partial.stoppedForBudget, true);
    assert.ok(partial.records.length > 0 && partial.records.length < 41);

    const rest = await readWireRecords(file, partial.state, { limitBytes: 10_000 });
    const types = [...partial.records, ...rest.records].map((item) => item.record.type);
    assert.equal(types.length, 41);
    // No line was read twice and none was skipped.
    const lines = [...partial.records, ...rest.records].map((item) => item.line);
    assert.deepEqual(lines, Array.from({ length: 41 }, (_, i) => i + 1));
  });
});

test("a window that starts at byte zero renumbers from the first line", async () => {
  await withWire(async (file) => {
    const records = [metadata(), turnPrompt({ turnId: 0 }), stepBegin({ turnId: 0 }), contentPart({ turnId: 0, part: { type: "text", text: "a" } }), turnEnded({ turnId: 0 })];
    await writeFile(file, wireText(records), "utf8");
    // A state whose cursor has been reset but whose line number was not: byte
    // zero is before the first line, so numbering has to restart there rather
    // than continue from a line the window no longer covers.
    const state = { ...emptyReaderState({ wirePath: file }), scanOffset: 0, line: 5 };

    const reread = await readWireRecords(file, state, { startOffset: 0, startLine: 1 });
    assert.equal(reread.status, "ok");
    assert.deepEqual(reread.records.map((item) => item.line), [1, 2, 3, 4, 5]);
    assert.deepEqual(reread.records.map((item) => item.record.type), [
      "metadata", "turn.prompt", "context.append_loop_event", "context.append_loop_event", "turn.ended",
    ]);
  });
});

test("a window that starts mid-file keeps the caller's absolute line numbers", async () => {
  await withWire(async (file) => {
    const records = [metadata(), turnPrompt({ turnId: 0 }), stepBegin({ turnId: 0 }), contentPart({ turnId: 0, part: { type: "text", text: "a" } }), turnEnded({ turnId: 0 })];
    await writeFile(file, wireText(records), "utf8");

    const all = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    const thirdOffset = all.records
      .slice(0, 2)
      .reduce((total, item) => total + Buffer.byteLength(JSON.stringify(item.record), "utf8") + 1, 0);
    const state = { ...all.state, scanOffset: all.state.scanOffset, line: 5 };

    const reread = await readWireRecords(file, state, { startOffset: thirdOffset, startLine: 3 });
    assert.deepEqual(reread.records.map((item) => item.line), [3, 4, 5]);
  });
});

test("a missing file is missing, not empty", async () => {
  await withWire(async (file) => {
    const result = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(result.status, "missing");
    assert.deepEqual(result.records, []);
  });
});

test("a complete line that is not JSON is counted, not fatal", async () => {
  await withWire(async (file) => {
    await writeFile(file, `${JSON.stringify(metadata())}\n{not json}\n${JSON.stringify(turnPrompt({ turnId: 0 }))}\n`, "utf8");
    const result = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(result.status, "ok");
    assert.equal(result.records.length, 3);
    assert.equal(result.records[1].malformed, true);
    assert.equal(result.records[2].record.type, "turn.prompt");
  });
});

test("a blank or unparseable first line is legacy, not a skipped gate", async () => {
  await withWire(async (file) => {
    await writeFile(file, `\n${JSON.stringify(turnPrompt({ turnId: 0 }))}\n`, "utf8");
    const blank = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(blank.status, "legacy", "a file whose first line is not metadata has no established protocol");

    await writeFile(file, `{not json}\n${JSON.stringify(turnPrompt({ turnId: 0 }))}\n`, "utf8");
    const broken = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(broken.status, "legacy");
  });
});

test("an unparseable min_protocol_version is refused, not assumed compatible", async () => {
  await withWire(async (file) => {
    await writeFile(file, wireText([
      metadata({ min_protocol_version: "not-a-version" }),
      turnPrompt({ turnId: 0 }),
    ]), "utf8");
    const result = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(result.status, "incompatible");
    assert.deepEqual(result.records, []);
  });
});

test("reset is reported once and never becomes part of the persisted state", async () => {
  await withWire(async (file) => {
    await writeFile(file, wireText([metadata(), turnPrompt({ turnId: 0, text: "first" })]), "utf8");
    const first = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(first.reset, undefined);

    await writeFile(file, wireText([metadata(), turnPrompt({ turnId: 9, text: "replacement" })]), "utf8");
    const second = await readWireRecords(file, first.state);
    assert.equal(second.reset, true);
    // The state must not carry the flag: a caller that persists it verbatim
    // would re-read the whole file on every later pass.
    assert.equal("reset" in second.state, false);
  });
});

test("startOffset without startLine is refused instead of shifting the numbering", async () => {
  await withWire(async (file) => {
    await writeFile(file, wireText([metadata(), turnPrompt({ turnId: 0 })]), "utf8");
    await assert.rejects(
      () => readWireRecords(file, emptyReaderState({ wirePath: file }), { startOffset: 0 }),
      /startOffset and startLine must be provided together/,
    );
  });
});
