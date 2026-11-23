import { describe, expect, test } from "claude-code/testing";
import {
  classifyCall,
  consulted,
  hashText,
  parseRecall,
  redact,
  summaryLine,
  titleOf,
  urisIn,
} from "./sources";

const EVENT = "viking://user/t/memories/events/2026/10/03/web版本未更新排查请求.md";
const PREF = "viking://user/t/memories/preferences/me/按钮状态.md";
const DOC = "viking://resources/team/release-checklist.md";

describe("recall", () => {
  test("memory items and the digest form, without directories or repeats", () => {
    const block = `<openviking-context>
<memory uri="${PREF}" score="0.62" detail="abstract">- x</memory>
<memory uri="${DOC}" score="0.31" detail="uri" />
<memory uri="viking://resources/" score="0.9" />
</openviking-context>`;
    expect(parseRecall(block)).toEqual([
      { uri: PREF, score: 0.62 },
      { uri: DOC, score: 0.31 },
    ]);
    expect(parseRecall(`- 发布流程 来源：${DOC}\n- 同上 来源：${DOC}`)).toEqual([
      { uri: DOC, score: 0 },
    ]);
  });
});

describe("tool calls", () => {
  test("ov find is a search, ov read opens files, writes and other commands are not lookups", () => {
    expect(
      classifyCall("Bash", {
        command: `ls ~/.openviking; ov find "lark web port" -n 3 2>&1 | head`,
      }),
    ).toEqual({
      opened: [],
      query: "lark web port",
    });
    expect(classifyCall("Bash", { command: `ov read ${DOC}` })).toEqual({
      opened: [DOC],
      query: null,
    });
    expect(classifyCall("Bash", { command: `ov add-memory "x"` })).toBeNull();
    expect(classifyCall("Bash", { command: `echo ${DOC} >> notes.md` })).toBeNull();
    const mcp = "mcp__plugin_openviking-memory_openviking__";
    expect(classifyCall(`${mcp}read`, { uris: [DOC] })).toEqual({ opened: [DOC], query: null });
    expect(classifyCall(`${mcp}search`, { query: "api_key=abc123 release" })).toEqual({
      opened: [],
      query: "api_key=[REDACTED] release",
    });
    expect(classifyCall(`${mcp}remember`, { text: "x" })).toBeNull();
  });

  test("output URIs: wrapped CLI lines are joined, scopes and directories dropped", () => {
    const out = `1. memory · score 0.43
   viking://user/t/memories/events/2026/10/03/web版本
   未更新排查请求.md
   see viking://resources and viking://resources/team/
2. ${DOC}`;
    expect(urisIn(out)).toEqual([EVENT, DOC]);
  });
});

describe("one answer", () => {
  test("search hits are not read in full; reads are; failed lookups count nothing", () => {
    const c = consulted({
      n: 1,
      recalled: [{ uri: PREF, score: 0.62 }],
      lookups: [
        { id: "a", query: "release", opened: [], found: [DOC, EVENT], isError: false },
        { id: "b", query: null, opened: [EVENT], found: [], isError: false },
        { id: "c", query: "x", opened: [], found: ["viking://resources/other.md"], isError: true },
      ],
    });
    expect(c.rows.map((r) => r.uri)).toEqual([EVENT, PREF, DOC]);
    expect(summaryLine(c)).toBe(
      "OV · 3 sources · 1 preference · 1 past event · 1 team doc · 1 read in full",
    );
    expect(titleOf(EVENT)).toBe("10/3 web版本未更新排查请求");
  });

  test("a title with a literal % does not throw", () => {
    expect(titleOf("viking://resources/docs/50% off.md")).toBe("50% off");
    expect(titleOf("viking://resources/docs/a%20b.md")).toBe("a b");
  });

  test("secrets are redacted", () => {
    expect(redact("Bearer abcdefghijklmnopqrstuvwxyz sk-abcdefghijklmnopqrstuvwx")).toBe(
      "Bearer [REDACTED] [REDACTED]",
    );
  });
});

describe("reply fingerprint", () => {
  test("ignores surrounding whitespace, tells different replies apart, keeps no text", () => {
    const reply = "L0 is the abstract, L1 the overview.";
    expect(hashText(`\n${reply}  `)).toBe(hashText(reply));
    expect(hashText(reply)).not.toBe(hashText("L0 is the abstract, L1 the overview!"));
    expect(hashText(reply)).not.toContain("abstract");
  });
});
