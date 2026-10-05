import { describe, expect, mock, test } from "claude-code/testing";
import type { On } from "claude-code";

const HOME = "/home/tester";
const PREF = "viking://user/tester/memories/preferences/me/按钮状态设计偏好.md";
const PRD = "viking://user/tester/memories/entities/项目文档/sample_feature_prd.md";
const SEARCH = "mcp__plugin_openviking-memory_openviking__search";

const WEAK = "viking://resources/someone/docs/admin_console_notes.md";
const RECALL = `<openviking-context>
<memory uri="${PREF}" type="preferences" score="0.62" detail="abstract">
- 不可用的操作，偏好把入口置灰并说明原因
</memory>
<memory uri="${WEAK}" type="resources" score="0.31" detail="abstract">
- Notes on the admin console layout
</memory>
</openviking-context>`;

const STARTUP = `<openviking-context source="startup">
<user-profile uri="viking://user/tester/memories/profile.md">
# Tester
- 职业：产品经理（as of 2026-07-08）
</user-profile>
<available-memories>
  viking://user/tester/memories/preferences/
    - me/a.md
</available-memories>
</openviking-context>`;

const ANSWER = `入口置灰，按你的偏好，这是本次回答的最后一段文字，足够长，可以用来匹配这一行。\n\n**OpenViking sources consulted:** ${PRD}`;

function world(
  on: On,
  store: Record<string, unknown> = {},
  recall: string | null = RECALL,
  toolText?: string,
) {
  const files: Record<string, string> = {
    [`${HOME}/.claude/plugins/installed_plugins.json`]: JSON.stringify({
      version: 2,
      plugins: {
        "openviking-memory@openviking": [
          {
            scope: "user",
            version: "0.5.2",
            installPath: `${HOME}/.claude/plugins/cache/openviking/openviking-memory/0.5.2`,
          },
        ],
      },
    }),
    [`${HOME}/.openviking/last_inject.md`]: STARTUP,
    [`${HOME}/.openviking/state/last-recall.json`]: JSON.stringify({
      reason: "ok",
      latency_ms: 1800,
      cc_session_id: "sess-1",
    }),
  };
  mock.env(on, { HOME });
  mock.store(on, store);
  mock.clock(on, { now: 1790000000000 });
  on("session.id", () => ({ value: "sess-1" }) as never);
  on("session.cwd", () => ({ value: "/work" }) as never);
  on("fs.stat", (_$, e) => {
    const text = files[e.path];
    if (text === undefined) return { deny: `ENOENT ${e.path}` };
    return { value: { kind: "file", size: text.length, mtimeMs: 1790000000000 } } as never;
  });
  on("fs.read", (_$, e) => {
    const text = files[e.path];
    if (text === undefined) return { deny: `ENOENT ${e.path}` };
    return { value: text } as never;
  });
  on("process.run", () => ({ value: { exitCode: 1, stdout: "", stderr: "no config" } }) as never);
  on("command.register", () => ({ value: { command: "openviking-usage" } }) as never);
  const opened: string[] = [];
  on("ui.open", (_$, e) => {
    opened.push(e.id);
    return { value: undefined } as never;
  });
  on("ui.status", () => ({ value: undefined }) as never);
  on("ui.toast", () => ({ value: undefined }) as never);
  on("session.start", () => ({ cwd: "/work" }) as never);
  on("turn.complete", (_$, e) => ({ text: e.answer }));
  on("classic.SessionStart", () => ({ additionalContext: [STARTUP] }));
  on("classic.UserPromptSubmit", () => ({ additionalContext: recall ? [recall] : undefined }));
  on(
    "tool.call",
    () => ({ result: "ok", text: toolText ?? `Found 1 item(s):\n- ${PRD}` }) as never,
  );
  // The engine's own drawing of a transcript row: its text.
  on("ui.render", ($, e) => {
    const { Text } = $.ui.resolve(e);
    return (globalThis as any).h(Text, null, String((e.props as { text?: string }).text ?? ""));
  });
  return { opened };
}

// One whole turn: the prompt, recall, a lookup, the end of the turn. (Tests cannot raise the
// engine's row appends, so cards are found by the rows' text, the fallback path.)
async function turn($: any) {
  await $.classic.UserPromptSubmit({
    prompt: "how should restricted actions look",
    session_id: "sess-1",
  } as never);
  await $.tool.call({
    tool: SEARCH,
    tool_use_id: "a",
    query: "enterprise permission model",
  } as never);
  await $.turn.complete({
    answer: ANSWER,
    durationMs: 10,
    isAborted: false,
    turnId: "t",
    reason: "answer",
  } as never);
}

async function row($: any, surface: string, component: string, requestId: string, props: object) {
  const ui = await $.ui.mount({ plugin: "ov-usage", surface, component, props, requestId });
  const texts = await ui.findAll({ type: "Text" });
  return texts.map((t: { text: string }) => t.text).join("\n");
}

for (const surface of ["terminal", "desktop"] as const) {
  describe(surface, () => {
    test("in conversation layout, an answer carries its card and the first prompt the overview", async ($: any, on) => {
      const { opened } = world(on, { layout: "inline" });
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await $.classic.SessionStart({ source: "startup", session_id: "sess-1" } as never);
      // no sidebar opened unasked
      expect(opened).not.toContain("ov-usage");
      await turn($);

      const reply = await row($, surface, "AssistantMessage", "r1", {
        text: ANSWER,
        isFirstOfReply: true,
      });
      expect(reply).toContain("入口置灰");
      expect(reply).toContain("OpenViking · this answer");
      // recalled (1 relevant + 1 less relevant) and found by Claude (1)
      expect(reply).toContain("✓ 3 OpenViking sources consulted");
      expect(reply).toContain("found by Claude");
      expect(reply).toContain("sample feature prd");
      expect(reply).toContain("按钮状态设计偏好");
      expect(reply).toContain("auto-recalled");
      expect(reply).toContain("Claude's own lookups");
      expect(reply).toContain("“enterprise permission model”");

      const prompt = await row($, surface, "UserMessage", "p1", {
        text: "how should restricted actions look",
        origin: { kind: "prompt" },
        isExpanded: false,
      });
      expect(prompt).toContain("All sessions");
      expect(prompt).toContain("your profile: 产品经理");
      expect(prompt).toContain("This conversation");
      expect(prompt).toContain("2 memories recalled across 1 prompt");
      expect(prompt).toContain("how should restricted actions look");

      // other rows are left as the engine draws them
      const other = await row($, surface, "AssistantMessage", "r0", {
        text: "an earlier unrelated reply",
        isFirstOfReply: true,
      });
      expect(other).not.toContain("OpenViking · this answer");
    });

    test("the answer card lists every source in full, however many", async ($: any, on) => {
      world(on, { layout: "inline", cardDetail: "high" });
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await $.classic.UserPromptSubmit({
        prompt: "what PRDs are being written now?",
        session_id: "sess-1",
      } as never);
      const docs = Array.from(
        { length: 8 },
        (_, i) =>
          `viking://user/tester/memories/events/2026/09/2${i}/一个相当长的事件标题用来确认卡片不会截断任何内容_${i}.md`,
      );
      await $.tool.call({
        tool: "mcp__plugin_openviking-memory_openviking__read",
        tool_use_id: "r",
        uris: docs,
      } as never);
      const answer = `Here is the list.\n\n**OpenViking sources consulted:**\n${docs.map((d) => `- ${d}`).join("\n")}`;
      await $.turn.complete({
        answer,
        durationMs: 10,
        isAborted: false,
        turnId: "t",
        reason: "answer",
      } as never);
      const card = await row($, surface, "AssistantMessage", "r9", {
        text: answer,
        isFirstOfReply: true,
      });
      // 8 read by Claude, plus 2 auto-recalled
      expect(card).toContain("✓ 10 OpenViking sources consulted");
      for (const d of docs) expect(card).toContain(d);
      expect(card).not.toContain("more");
    });

    test("recalled memories count as consulted, less relevant ones stay folded", async ($: any, on) => {
      world(on, { layout: "inline" });
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await $.classic.UserPromptSubmit({
        prompt: "how should restricted actions look",
        session_id: "sess-1",
      } as never);
      const used =
        "按你的偏好（按钮状态设计偏好），受限操作的入口置灰，并在旁边说明原因，而不是等接口报错。";
      await $.turn.complete({
        answer: used,
        durationMs: 10,
        isAborted: false,
        turnId: "t",
        reason: "answer",
      } as never);
      const ui = await $.ui.mount({
        plugin: "ov-usage",
        surface,
        component: "AssistantMessage",
        props: { text: used, isFirstOfReply: true },
        requestId: "r2",
      });
      const read = async () =>
        (await ui.findAll({ type: "Text" })).map((t: { text: string }) => t.text).join("\n");
      const card = await read();
      expect(card).toContain("✓ 2 OpenViking sources consulted");
      expect(card).toContain("按钮状态设计偏好");
      // the less relevant one waits behind +1 more
      expect(card).not.toContain("admin console notes");
      expect((await ui.find({ key: "toggle-card-more" }))?.text).toContain("[+1 more]");
      await ui.press({ key: "toggle-card-more" });
      expect(await read()).toContain("admin console notes");
    });

    test("an answer with nothing recalled and nothing looked up gets no card", async ($: any, on) => {
      world(on, { layout: "inline" }, null);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await $.classic.UserPromptSubmit({
        prompt: "unrelated question",
        session_id: "sess-1",
      } as never);
      const plain =
        "A plain answer that names no memory at all, long enough to be matched to its row.";
      await $.turn.complete({
        answer: plain,
        durationMs: 10,
        isAborted: false,
        turnId: "t",
        reason: "answer",
      } as never);
      const text = await row($, surface, "AssistantMessage", "r3", {
        text: plain,
        isFirstOfReply: true,
      });
      expect(text).toContain("A plain answer");
      expect(text).not.toContain("OpenViking · this answer");
    });

    test("prose that mentions viking:// in a search summary is not a source", async ($: any, on) => {
      const prose = `Found 1 item(s):\n- [resource 59%] ${PRD}\n    规则要求列出本次实际引用的所有viking://格式具体来源URI，不能列出未使用的URI`;
      world(on, { layout: "inline", cardDetail: "high" }, null, prose);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await $.classic.UserPromptSubmit({ prompt: "rules", session_id: "sess-1" } as never);
      await $.tool.call({ tool: SEARCH, tool_use_id: "s1", query: "rules" } as never);
      const a = "An answer long enough to be matched to its row, about the disclosure rules.";
      await $.turn.complete({
        answer: a,
        durationMs: 10,
        isAborted: false,
        turnId: "t",
        reason: "answer",
      } as never);
      const card = await row($, surface, "AssistantMessage", "r4", {
        text: a,
        isFirstOfReply: true,
      });
      expect(card).toContain("✓ 1 OpenViking source consulted");
      expect(card).not.toContain("格式具体来源URI");
    });

    test("low shows titles only, high adds URIs; copies of one document share a row", async ($: any, on) => {
      const copies = [1, 2, 3].map(
        (i) => `viking://resources/snap-${i}/AGENTS/OpenViking_项目规则_1.md`,
      );
      const text = `Found 3 item(s):\n${copies.map((u) => `- [resource 59%] ${u}`).join("\n")}`;
      world(on, { layout: "inline" }, null, text);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await $.classic.UserPromptSubmit({ prompt: "rules", session_id: "sess-1" } as never);
      await $.tool.call({ tool: SEARCH, tool_use_id: "s2", query: "rules" } as never);
      const a = "Another answer long enough to be matched to its row, about project rules.";
      await $.turn.complete({
        answer: a,
        durationMs: 10,
        isAborted: false,
        turnId: "t",
        reason: "answer",
      } as never);
      const ui = await $.ui.mount({
        plugin: "ov-usage",
        surface,
        component: "AssistantMessage",
        props: { text: a, isFirstOfReply: true },
        requestId: "r5",
      });
      const read = async () =>
        (await ui.findAll({ type: "Text" })).map((t: { text: string }) => t.text).join("\n");
      const low = await read();
      expect(low).toContain("✓ 3 OpenViking sources consulted");
      expect(low).toContain("OpenViking 项目规则 1 ×3");
      expect(low).not.toContain("viking://resources/snap-1");
      await $.command.run({ command: "openviking-usage", args: "card high" } as never);
      const high = await read();
      for (const u of copies) expect(high).toContain(u);
    });

    test("more than 10 sources: the 10 most relevant show, the rest under +N more", async ($: any, on) => {
      const docs = Array.from({ length: 12 }, (_, i) => ({
        uri: `viking://resources/docs/source_${String.fromCharCode(97 + i)}.md`,
        score: 90 - i,
      }));
      const text = `Found 12 item(s):\n${docs.map((d) => `- [resource ${d.score}%] ${d.uri}`).join("\n")}`;
      world(on, { layout: "inline" }, null, text);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await $.classic.UserPromptSubmit({ prompt: "sources", session_id: "sess-1" } as never);
      await $.tool.call({ tool: SEARCH, tool_use_id: "s3", query: "sources" } as never);
      const a = "A third answer long enough to be matched to its row, citing many sources.";
      await $.turn.complete({
        answer: a,
        durationMs: 10,
        isAborted: false,
        turnId: "t",
        reason: "answer",
      } as never);
      const ui = await $.ui.mount({
        plugin: "ov-usage",
        surface,
        component: "AssistantMessage",
        props: { text: a, isFirstOfReply: true },
        requestId: "r6",
      });
      const read = async () =>
        (await ui.findAll({ type: "Text" })).map((t: { text: string }) => t.text).join("\n");
      const top = await read();
      expect(top).toContain("✓ 12 OpenViking sources consulted");
      expect(top).toContain("source a");
      expect(top).toContain("source j");
      expect(top).not.toContain("source k");
      expect((await ui.find({ key: "toggle-card-more" }))?.text).toContain("[+2 more]");
      await ui.press({ key: "toggle-card-more" });
      expect(await read()).toContain("source l");
    });

    test("in sidebar layout no cards are drawn, and the command switches layouts", async ($: any, on) => {
      const { opened } = world(on);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      expect(opened).toContain("ov-usage");
      await turn($);
      const ui = await $.ui.mount({
        plugin: "ov-usage",
        surface,
        component: "AssistantMessage",
        props: { text: ANSWER, isFirstOfReply: true },
        requestId: "r1",
      });
      const read = async () =>
        (await ui.findAll({ type: "Text" })).map((t: { text: string }) => t.text).join("\n");
      expect(await read()).not.toContain("OpenViking · this answer");

      // the same row redraws when the layout changes
      const res = await $.command.run({
        command: "openviking-usage",
        args: "show conversation",
      } as never);
      expect(res.text).toContain("cards in the conversation");
      expect(await read()).toContain("OpenViking · this answer");

      await $.command.run({ command: "openviking-usage", args: "show sidebar" } as never);
      expect(await read()).not.toContain("OpenViking · this answer");
    });
  });
}
