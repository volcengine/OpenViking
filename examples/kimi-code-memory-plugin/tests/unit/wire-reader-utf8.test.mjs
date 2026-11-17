/**
 * The reader against text that does not fit in one read.
 *
 * The first implementation decoded each chunk on its own, so a UTF-8 character
 * split across two reads became replacement characters and the record failed to
 * parse. A Chinese prompt or an emoji is enough to trigger it, which is why this
 * is a correctness bug and not a curiosity.
 */

import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { emptyReaderState, readWireRecords } from "../../scripts/wire-reader.mjs";
import { metadata, turnEnded, turnPrompt, wireText } from "../fixtures/wire-fixtures.mjs";

async function withWire(fn) {
  const dir = await mkdtemp(join(tmpdir(), "kc-wire-utf8-"));
  const file = join(dir, "wire.jsonl");
  try {
    return await fn(file);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
}

test("a multi-byte character split across a read boundary survives intact", async () => {
  await withWire(async (file) => {
    // Long enough that the record crosses the reader's 256 KB chunk, and placed
    // so the split lands inside the CJK characters and an emoji.
    // The first CJK byte must land just before the 256 KB chunk boundary, or
    // the whole fragment lands in the second chunk and the test proves nothing.
    const lineStart = Buffer.byteLength(JSON.stringify(metadata()), "utf8") + 1;
    const probe = JSON.stringify(turnPrompt({ turnId: 0, text: "中" }));
    const prefixBytes = Buffer.byteLength(probe.slice(0, probe.indexOf("中")), "utf8");
    const filler = "a".repeat(256 * 1024 - 1 - lineStart - prefixBytes);
    const text = `${filler}中文提示🙂继续`;
    await writeFile(file, wireText([metadata(), turnPrompt({ turnId: 0, text }), turnEnded({ turnId: 0 })]), "utf8");

    const result = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(result.status, "ok");
    assert.equal(result.records.length, 3);
    const prompt = result.records[1].record;
    assert.equal(prompt.input[0].text.endsWith("中文提示🙂继续"), true);
    assert.equal(prompt.input[0].text.includes("\uFFFD"), false, "a replacement character means the decode was split");
  });
});

test("an emoji at the very start of a chunk does not corrupt the line", async () => {
  await withWire(async (file) => {
    const records = [metadata()];
    for (let i = 0; i < 300; i += 1) {
      records.push(turnPrompt({ turnId: i, text: `🙂 line ${i} 中文` }));
    }
    await writeFile(file, wireText(records), "utf8");

    // A tiny budget forces many small reads over the same bytes.
    const first = await readWireRecords(file, emptyReaderState({ wirePath: file }), { limitBytes: 4096 });
    assert.equal(first.status, "ok");
    for (const entry of first.records) {
      if (!entry.record) continue;
      const text = entry.record.input?.[0]?.text ?? "";
      assert.equal(text.includes("\uFFFD"), false, `line ${entry.line} was decoded in pieces`);
    }
  });
});

test("every record carries its own byte offset", async () => {
  await withWire(async (file) => {
    const body = wireText([metadata(), turnPrompt({ turnId: 0, text: "中文" }), turnEnded({ turnId: 0 })]);
    await writeFile(file, body, "utf8");

    const result = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(result.records.length, 3);
    const bytes = Buffer.from(body, "utf8");
    for (const entry of result.records) {
      // The offset must point at the start of that record's own line, which is
      // what the capture stage uses to re-read an open turn from its opening.
      const slice = bytes.subarray(entry.offset);
      const firstLine = slice.subarray(0, slice.indexOf(0x0a)).toString("utf8");
      assert.equal(JSON.parse(firstLine).type, entry.record.type, `offset for line ${entry.line}`);
    }
  });
});

test("a resumed read keeps its offsets and does not re-report delivered bytes", async () => {
  await withWire(async (file) => {
    await writeFile(file, wireText([metadata(), turnPrompt({ turnId: 0, text: "第一轮" })]), "utf8");
    const first = await readWireRecords(file, emptyReaderState({ wirePath: file }));
    assert.equal(first.records.length, 2);
    const endOfFirst = first.state.scanOffset;

    const { appendFile } = await import("node:fs/promises");
    await appendFile(file, wireText([turnPrompt({ turnId: 1, text: "第二轮🙂" })]), "utf8");

    const second = await readWireRecords(file, first.state);
    assert.equal(second.status, "ok");
    assert.equal(second.records.length, 1);
    assert.equal(second.records[0].line, 3);
    assert.ok(second.records[0].offset >= endOfFirst);
    assert.equal(second.records[0].record.input[0].text, "第二轮🙂");
  });
});
