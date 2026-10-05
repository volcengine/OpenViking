import { describe, expect, mock, test } from "claude-code/testing";
import type { On } from "claude-code";

const HOME = "/home/tester";
const PREF = "viking://user/tester/memories/preferences/me/按钮状态设计偏好.md";
const EVENT = "viking://user/tester/memories/events/2026/09/29/版本发布计划讨论.md";
const EMPTY_DOC = "viking://resources/someone/files/say_06_05.aiff/say_06_05.aiff_29.md";
const DEBUG_LOG = "viking://resources/someone/conversations/Codex_conversation_38.md";
const API_KEY = "sk-test-SHOULD-NEVER-RENDER-1234567890";

const RECALL = `<openviking-context>
Relevant memory from OpenViking. Use the search/read MCP tools to expand URIs.
<memory uri="${PREF}" type="preferences" score="0.62" detail="abstract">
- 不可用的操作，偏好把入口置灰并说明原因
</memory>
<memory uri="${DEBUG_LOG}" type="resources" score="0.49" detail="uri" />
<memory uri="${EVENT}" type="events" score="0.58" detail="full">
# Summary
2026-09-29 团队讨论了下个版本的发布计划
</memory>
<memory uri="${EMPTY_DOC}" type="resources" score="0.56" detail="abstract">
The current document is an empty Markdown file named say_06_05.aiff_29.md.
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
    - me/b.md
  viking://user/tester/memories/entities/
    - 人员/x.md
</available-memories>
</openviking-context>`;

const SETTINGS = {
  server: "https://ov.example.com/openviking",
  apiKeySet: true,
  autoRecall: true,
  scoreThreshold: 0.45,
  recallLimit: 10,
  recallTokenBudget: 2000,
  recallPeerScope: "actor",
  autoCapture: true,
  commitTurnThreshold: 8,
  startupInject: true,
  profileTokenBudget: 10000,
  mcpEnabled: true,
  error: null,
};

// The engine beneath the plugin: a fake home with OpenViking's state files and a config loader.
function world(
  on: On,
  opts: {
    settingsFail?: boolean;
    settings?: Partial<typeof SETTINGS>;
    files?: Record<string, string>;
  } = {},
) {
  const files: Record<string, string> = {
    [`${HOME}/.openviking/last_inject.md`]: STARTUP,
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
    [`${HOME}/.openviking/state/last-capture.json`]: JSON.stringify({
      commit_count: 2,
      cc_session_id: "sess-1",
    }),
    [`${HOME}/.openviking/state/last-recall.json`]: JSON.stringify({
      reason: "ok",
      latency_ms: 2200,
      tokens_used: 528,
      cc_session_id: "sess-1",
    }),
    ...opts.files,
  };
  const ran: { argv: readonly string[]; env?: Record<string, string> }[] = [];
  mock.env(on, { HOME });
  mock.store(on);
  mock.clock(on, { now: 1790000000000 });
  on("session.id", () => ({ value: "sess-1" }) as never);
  on("ui.open", () => ({ value: undefined }) as never);
  on("ui.status", () => ({ value: undefined }) as never);
  on("ui.toast", () => ({ value: undefined }) as never);
  on("turn.complete", (_$, e) => ({ text: e.answer }));
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
  on("session.cwd", () => ({ value: "/work" }) as never);
  on("process.run", (_$, e) => {
    ran.push({ argv: e.argv, env: e.init?.env });
    if (opts.settingsFail)
      return {
        value: { exitCode: 0, stdout: '{"error":"Cannot find module config.mjs"}\n', stderr: "" },
      } as never;
    return {
      value: {
        exitCode: 0,
        stdout: `${JSON.stringify({ ...SETTINGS, ...opts.settings })}\n`,
        stderr: "",
      },
    } as never;
  });
  on("classic.UserPromptSubmit", () => ({ additionalContext: [RECALL] }));
  on("command.register", () => ({ value: { command: "openviking-usage" } }) as never);
  on("session.start", () => ({ cwd: "/work" }) as never);
  return { ran };
}

async function prompt($: any, text: string) {
  return $.classic.UserPromptSubmit({ prompt: text, session_id: "sess-1" } as never);
}

async function mountPane($: any, surface: "terminal" | "desktop") {
  return $.ui.mount({
    plugin: "ov-usage",
    surface,
    component: "Pane",
    props: { title: "OV-Usage", isFocused: true, bodyColumns: 80 },
    requestId: "ov-usage",
  });
}

async function allText(ui: any): Promise<string> {
  const texts = await ui.findAll({ type: "Text" });
  return texts.map((t: { text: string }) => t.text).join("\n");
}

for (const surface of ["terminal", "desktop"] as const) {
  describe(surface, () => {
    test("session start loads startup context, lifetime stats and settings", async ($: any, on) => {
      const { ran } = world(on);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      const ui = await mountPane($, surface);
      const text = await allText(ui);

      expect(text).toContain("All sessions");
      expect(text).toContain("This conversation");
      expect(text).toContain("Startup: 3 memories indexed");
      expect(text).toContain("your profile: 产品经理");
      // the settings script ran through node, from the install Claude Code's registry names
      expect(ran.some((r) => r.argv[0] === "node")).toBe(true);
      expect(ran.at(-1)?.env).toEqual({
        OV_PLUGIN_SCRIPTS: `${HOME}/.claude/plugins/cache/openviking/openviking-memory/0.5.2/scripts`,
      });
      // settings start collapsed, showing only recall/capture state
      const toggle = await ui.find({ key: "toggle-settings" });
      expect(toggle?.text).toContain("▸ Settings");
      expect(text).toContain("Auto-recall on · auto-capture on");
      expect(text).not.toContain("threshold 0.45");
    });

    test("a recall shows personal memories and collapses weak docs", async ($: any, on) => {
      world(on);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await prompt($, "how should restricted actions look in the admin console?");
      const ui = await mountPane($, surface);
      // everything recalled counts as consulted: 2 relevant plus 1 less relevant (the empty doc is ignored)
      expect(await allText(ui)).toContain("✓ 3 OpenViking sources consulted");
      expect(await allText(ui)).toContain("Your preference");
      // totals across sessions are counted from this prompt and the session's last capture
      expect(await allText(ui)).toContain("Memories auto-added to 1 prompt");
      expect(await allText(ui)).toContain("2 memory updates saved from 1 conversation");
      expect(await allText(ui)).not.toContain("empty document ignored");
      await ui.press({ key: "toggle-details" });
      const text = await allText(ui);

      expect(text).toContain("Your preference");
      expect(text).toContain("按钮状态设计偏好");
      expect(text).toContain("Past event");
      expect(text).toContain("9/29 版本发布计划讨论");
      expect(text).toContain("▤ 1 less relevant match");
      expect(text).toContain("1 empty document skipped");
      expect(text).toContain("This answer · 2.2s");
      // weak items hidden until "show"; empty documents never shown
      expect(text).not.toContain("Codex_conversation_38");
      await ui.press({ key: "toggle-weak" });
      const shown = await allText(ui);
      expect(shown).toContain("Codex_conversation_38");
      expect(shown).not.toContain("say_06_05");
    });

    test("mute keeps a memory out of the next recall", async ($: any, on) => {
      world(on);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await prompt($, "first prompt about the admin console");
      const ui = await mountPane($, surface);
      // mute buttons sit under [details]
      await ui.press({ key: "toggle-details" });
      await ui.press({ key: `mute-${PREF}` });

      const next = await prompt($, "second prompt about the admin console");
      const injected = (next.additionalContext ?? []).join("\n");
      expect(injected).not.toContain(PREF);
      expect(injected).toContain(EVENT);
      expect(await allText(ui)).toContain(
        "1 muted for this session · 1 held back from this answer",
      );

      await ui.press({ key: "unmute-all" });
      const again = await prompt($, "third prompt about the admin console");
      expect((again.additionalContext ?? []).join("\n")).toContain(PREF);
    });

    test("repeat recalls are marked still in context", async ($: any, on) => {
      world(on);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await prompt($, "first prompt about the admin console");
      await prompt($, "second prompt about the admin console");
      const ui = await mountPane($, surface);
      expect(await allText(ui)).not.toContain("already in context");
      await ui.press({ key: "toggle-details" });
      expect(await allText(ui)).toContain("3 already in context from earlier prompts");
    });

    test("opened and cited memories are marked", async ($: any, on) => {
      world(on);
      on("tool.call", () => ({ result: "ok", text: `read ${EVENT}` }) as never);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await prompt($, "how should restricted actions look in the admin console?");
      await $.tool.call({
        tool: "mcp__plugin_openviking-memory_openviking__read",
        tool_use_id: "t1",
        uris: [EVENT],
      } as never);
      await $.turn.complete({
        answer: "按你的偏好（按钮状态设计偏好），入口置灰。",
        durationMs: 10,
        isAborted: false,
        turnId: "turn-1",
        reason: "answer",
      } as never);
      const ui = await mountPane($, surface);
      const text = await allText(ui);
      expect(text).toContain("✓ 3 OpenViking sources consulted");
      expect(text).toContain("✓ opened");
      expect(text).toContain("3 OpenViking sources consulted this session");
      expect(text).toContain(
        "✓ 3 OpenViking sources consulted · 1 preference · 1 past event · 1 team doc · 1 read in full",
      );
      expect(text).not.toContain("read in full by Claude");
      await ui.press({ key: "toggle-details" });
      expect(await allText(ui)).toContain("1 read in full by Claude");
      // the read also shows as one of Claude's own lookups
      expect(text).toContain("Claude's own lookups");
      expect(text).toContain("▤ Read");
    });

    test("with auto-recall off, nothing about it shows, even under details", async ($: any, on) => {
      world(on, { settings: { autoRecall: false } });
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await prompt($, "how should restricted actions look in the admin console?");
      const ui = await mountPane($, surface);
      await ui.press({ key: "toggle-details" });
      const text = await allText(ui);
      expect(text).toContain("Auto-recall off");
      expect(text).not.toContain("Auto-recalled");
      expect(text).not.toContain("按钮状态设计偏好");
      expect(text).not.toContain("less relevant");
      expect(text).not.toContain("auto-added");
      expect(text).not.toContain("recalled across");
      expect(text).not.toContain("2.2s");
      expect(text).toContain("No OpenViking sources for this answer.");
    });

    test("expanded settings never show the API key", async ($: any, on) => {
      world(on);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      const ui = await mountPane($, surface);
      await ui.press({ key: "toggle-settings" });
      const text = await allText(ui);
      expect(text).toContain("ov.example.com");
      expect(text).toContain("API key set");
      expect(text).toContain("threshold 0.45");
      expect(text).toContain("saves to memory every 8 turns");
      expect(text).not.toContain(API_KEY);
    });

    test("a broken config loader shows an error instead of failing", async ($: any, on) => {
      world(on, { settingsFail: true });
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      const ui = await mountPane($, surface);
      await ui.press({ key: "toggle-settings" });
      expect(await allText(ui)).toContain(
        "Couldn't read OpenViking's config: openviking-memory 0.5.2: Cannot find module config.mjs",
      );
    });

    test("an install scoped to this project wins over the user-wide one", async ($: any, on) => {
      const { ran } = world(on, {
        files: {
          [`${HOME}/.claude/plugins/installed_plugins.json`]: JSON.stringify({
            version: 2,
            plugins: {
              "openviking-memory@openviking": [
                { scope: "user", version: "0.5.2", installPath: "/cache/0.5.2" },
                {
                  scope: "project",
                  projectPath: "/work",
                  version: "0.6.0",
                  installPath: "/cache/0.6.0",
                },
              ],
            },
          }),
        },
      });
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      expect(ran.at(-1)?.env).toEqual({ OV_PLUGIN_SCRIPTS: "/cache/0.6.0/scripts" });
    });

    test("a recall snapshot from an earlier prompt is not read as this prompt's", async ($: any, on) => {
      world(on, {
        files: {
          [`${HOME}/.openviking/state/last-recall.json`]: JSON.stringify({
            reason: "ok",
            latency_ms: 9900,
            cc_session_id: "sess-1",
            ts: 1790000000000 - 60000,
          }),
        },
      });
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await prompt($, "how should restricted actions look in the admin console?");
      const ui = await mountPane($, surface);
      expect(await allText(ui)).not.toContain("9.9s");
    });
  });
}
