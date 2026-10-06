import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyCall,
  consulted,
  expandedLines,
  parseRecall,
  redact,
  summaryLine,
  titleOf,
  urisIn,
} from "../sources.mjs";

const EVENT = "viking://user/t/memories/events/2026/10/03/web版本未更新排查请求.md";
const PREF = "viking://user/t/memories/preferences/me/按钮状态.md";
const DOC = "viking://resources/team/release-checklist.md";

test("recall parsing keeps sources, scores, and digest entries", () => {
  const block = `<openviking-context>
<memory uri="${PREF}" score="0.62" detail="abstract">- x</memory>
<memory uri="${DOC}" score="0.31" detail="uri" />
<memory uri="viking://resources/" score="0.9" />
</openviking-context>`;
  assert.deepEqual(parseRecall(block), [
    { uri: PREF, score: 0.62 },
    { uri: DOC, score: 0.31 },
  ]);
  assert.deepEqual(parseRecall(`- 发布流程 来源：${DOC}\n- 同上 来源：${DOC}`), [
    { uri: DOC, score: 0 },
  ]);
});

test("tool classification separates searches, reads, and writes", () => {
  assert.deepEqual(classifyCall("Bash", {
    command: `ls ~/.openviking; ov find "lark web port" -n 3 2>&1 | head`,
  }), { opened: [], query: "lark web port" });
  assert.deepEqual(classifyCall("Bash", { command: `ov read ${DOC}` }), {
    opened: [DOC],
    query: null,
  });
  assert.equal(classifyCall("Bash", { command: `ov add-memory "x"` }), null);
  assert.equal(classifyCall("Bash", { command: `echo ${DOC} >> notes.md` }), null);
  const mcp = "mcp__plugin_openviking-memory_openviking__";
  assert.deepEqual(classifyCall(`${mcp}read`, { uris: [DOC] }), { opened: [DOC], query: null });
  assert.deepEqual(classifyCall(`${mcp}search`, { query: "api_key=abc123 release" }), {
    opened: [],
    query: "api_key=[REDACTED] release",
  });
  assert.equal(classifyCall(`${mcp}remember`, { text: "x" }), null);
});

test("wrapped output URIs are joined and directories are discarded", () => {
  const output = `1. memory · score 0.43
   viking://user/t/memories/events/2026/10/03/web版本
   未更新排查请求.md
   see viking://resources and viking://resources/team/
2. ${DOC}`;
  assert.deepEqual(urisIn(output), [EVENT, DOC]);
});

test("summary distinguishes search hits from full reads", () => {
  const turn = {
    recalled: [{ uri: PREF, score: 0.62 }],
    lookups: [
      { id: "a", query: "release", opened: [], found: [DOC, EVENT], isError: false },
      { id: "b", query: null, opened: [EVENT], found: [], isError: false },
      { id: "c", query: "x", opened: [], found: ["viking://resources/other.md"], isError: true },
    ],
  };
  const result = consulted(turn);
  assert.deepEqual(result.rows.map((row) => row.uri), [EVENT, PREF, DOC]);
  assert.equal(
    summaryLine(result),
    "OV · 3 sources · 1 preference · 1 past event · 1 team doc · 1 read in full",
  );
  assert.equal(titleOf(EVENT), "10/3 web版本未更新排查请求");
  assert.match(expandedLines(turn, result).join("\n"), /Codex lookups/);
});

test("common credential shapes are redacted", () => {
  assert.equal(
    redact("Bearer abcdefghijklmnopqrstuvwxyz sk-abcdefghijklmnopqrstuvwx"),
    "Bearer [REDACTED] [REDACTED]",
  );
});

test("expanded source titles tolerate literal percent and retain provenance URIs", () => {
  const uri = "viking://resources/team/100%-complete.md";
  assert.equal(titleOf(uri), "100%-complete");
  assert.match(expandedLines({ recalled: [{ uri, score: 0.5 }], lookups: [] }).join("\n"), /viking:\/\/resources\/team\/100%-complete.md/);
});
