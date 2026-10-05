// QA suite: a new user whose answers from Claude never mention OpenViking, list
// sources or name a viking:// URI. Everything OV-Usage shows has to come from
// what was retrieved, not from the answer's wording.
import { describe, expect, mock, test } from "claude-code/testing";
import type { On } from "claude-code";

const HOME = "/home/newuser";
const OV = "mcp__plugin_openviking-memory_openviking__";
const PREF = "viking://user/newuser/memories/preferences/me/code_style.md";
const NOTE = "viking://user/newuser/memories/entities/projects/billing_service.md";
const LOW = "viking://resources/team/docs/old_onboarding.md";
const GUIDE = "viking://resources/team/docs/deploy_guide.md";
const RUNBOOK = "viking://resources/team/docs/incident_runbook.md";
const KEY = "sk-live-THISKEYMUSTNEVERSHOW-1234567890";

const RECALL = `<openviking-context>
<memory uri="${PREF}" type="preferences" score="0.71" detail="abstract">
- Prefers small functions and early returns
</memory>
<memory uri="${NOTE}" type="entities" score="0.58" detail="abstract">
- Billing service owns invoices and refunds
</memory>
<memory uri="${LOW}" type="resources" score="0.33" detail="abstract">
- Old onboarding notes
</memory>
</openviking-context>`;

const SETTINGS = {
  server: "https://ov.example.com",
  apiKeySet: true,
  autoRecall: true,
  scoreThreshold: 0.35,
  recallLimit: 6,
  recallTokenBudget: 2000,
  recallPeerScope: "actor",
  autoCapture: true,
  commitTokenThreshold: 20000,
  startupInject: true,
  profileTokenBudget: 4000,
  mcpEnabled: true,
  error: null,
};

type World = {
  // nothing under ~/.openviking and no OpenViking plugin installed
  isFresh?: boolean;
  recall?: string | null;
  settings?: Partial<typeof SETTINGS>;
  store?: Record<string, unknown>;
  // what each tool call returns, by tool name
  tools?: Record<string, { text?: string; isError?: boolean }>;
  // the startup context OpenViking injects (profile + memory index)
  startup?: string;
};

function world(on: On, w: World = {}) {
  const files: Record<string, string> = w.isFresh
    ? {}
    : {
        [`${HOME}/.claude/plugins/installed_plugins.json`]: JSON.stringify({
          version: 2,
          plugins: {
            "openviking-memory@openviking": [
              {
                scope: "user",
                version: "0.6.4",
                installPath: `${HOME}/.claude/plugins/cache/openviking/openviking-memory/0.6.4`,
              },
            ],
          },
        }),
        [`${HOME}/.openviking/state/last-recall.json`]: JSON.stringify({
          reason: w.recall === null ? "no_results" : "ok",
          latency_ms: 900,
          cc_session_id: "sess-1",
        }),
      };
  mock.env(on, { HOME });
  // A store the test can inspect afterwards.
  const store: Record<string, unknown> = { ...w.store };
  on("store.get", (_$, e) => ({ value: store[e.key] }) as never);
  on("store.set", (_$, e) => {
    store[e.key] = e.value;
    return { value: undefined } as never;
  });
  on("store.delete", (_$, e) => {
    delete store[e.key];
    return { value: undefined } as never;
  });
  on("store.keys", () => ({ value: Object.keys(store) }) as never);
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
  on(
    "process.run",
    () =>
      (w.isFresh
        ? { value: { exitCode: 1, stdout: "", stderr: "Cannot find module config.mjs" } }
        : {
            value: {
              exitCode: 0,
              stdout: `${JSON.stringify({ ...SETTINGS, ...w.settings })}\n`,
              stderr: "",
            },
          }) as never,
  );
  on("command.register", () => ({ value: { command: "openviking-usage" } }) as never);
  on("ui.open", () => ({ value: undefined }) as never);
  const status: (string | undefined)[] = [];
  on("ui.status", (_$, e) => {
    status.push(e.text);
    return { value: undefined } as never;
  });
  on("ui.toast", () => ({ value: undefined }) as never);
  on("session.start", () => ({ cwd: "/work" }) as never);
  on("turn.complete", (_$, e) => ({ text: e.answer }));
  on("classic.SessionStart", () => ({ additionalContext: w.startup ? [w.startup] : undefined }));
  on("classic.UserPromptSubmit", () => ({ additionalContext: w.recall ? [w.recall] : undefined }));
  on("tool.call", (_$, e) => {
    const r = w.tools?.[e.tool] ?? { text: "" };
    return (
      r.isError
        ? { result: "error", text: r.text ?? "failed", isError: true }
        : { result: "ok", text: r.text ?? "" }
    ) as never;
  });
  on("ui.render", ($, e) => {
    const { Text } = $.ui.resolve(e);
    return (globalThis as any).h(Text, null, String((e.props as { text?: string }).text ?? ""));
  });
  return { store, status };
}

async function start($: any, layout?: "inline" | "pane") {
  await $.session.start({ source: "startup", cwd: "/work" } as never);
  await $.classic.SessionStart({ source: "startup", session_id: "sess-1" } as never);
  if (layout)
    await $.command.run({
      command: "openviking-usage",
      args: `show ${layout === "inline" ? "conversation" : "sidebar"}`,
    } as never);
}

async function ask($: any, prompt: string, answer: string, calls: Record<string, unknown>[] = []) {
  await $.classic.UserPromptSubmit({ prompt, session_id: "sess-1" } as never);
  let i = 0;
  for (const c of calls)
    await $.tool.call({ tool_use_id: `t${++i}-${prompt.length}`, ...c } as never);
  await $.turn.complete({
    answer,
    durationMs: 10,
    isAborted: false,
    turnId: "t",
    reason: "answer",
  } as never);
}

async function texts(ui: any) {
  return (await ui.findAll({ type: "Text" })).map((t: { text: string }) => t.text).join("\n");
}

async function card($: any, surface: string, answer: string, id: string) {
  const ui = await $.ui.mount({
    plugin: "ov-usage",
    surface,
    component: "AssistantMessage",
    props: { text: answer, isFirstOfReply: true },
    requestId: id,
  });
  return { ui, text: await texts(ui) };
}

async function pane($: any, surface: string) {
  const ui = await $.ui.mount({
    plugin: "ov-usage",
    surface,
    component: "Pane",
    props: { title: "OV-Usage", isFocused: true, bodyColumns: 80 },
    requestId: "ov-usage",
  });
  return { ui, text: await texts(ui) };
}

// Plain answers: no "OpenViking", no URI, no source list.
const A1 =
  "Use early returns and keep the handler under thirty lines. Split validation into its own function, then call the refund path only after the invoice check passes.";
const A2 =
  "Run the deploy script from the release branch, wait for the health check to go green, then flip traffic in two steps of fifty percent each.";

for (const surface of ["terminal", "desktop"] as const) {
  describe(`new user · ${surface}`, () => {
    test("1. first run with nothing installed: no crash, the pane says what to do", async ($: any, on) => {
      world(on, { isFresh: true });
      await start($);
      const { text } = await pane($, surface);
      expect(text).not.toContain("failed to draw");
      expect(text).toContain("Send a prompt to see which OpenViking sources it draws on.");
    });

    test("2. auto-recall only, plain answer: everything recalled counts, the weak one folds", async ($: any, on) => {
      world(on, { recall: RECALL });
      await start($, "inline");
      await ask($, "how should I structure the refund handler?", A1);
      const { ui, text } = await card($, surface, A1, "a1");
      expect(text).toContain(
        "✓ 3 OpenViking sources consulted · 1 preference · 1 work memory · 1 team doc",
      );
      expect(text).toContain("code style");
      expect(text).toContain("billing service");
      expect(text).not.toContain("old onboarding");
      expect((await ui.find({ key: "toggle-card-more" }))?.text).toContain("[+1 more]");
    });

    test("3. auto-recall off, Claude searches then reads: the file it opened ranks first", async ($: any, on) => {
      world(on, {
        recall: null,
        settings: { autoRecall: false },
        tools: {
          [`${OV}search`]: {
            text: `Found 2 item(s):\n- [resource 61%] ${RUNBOOK}\n- [resource 55%] ${GUIDE}`,
          },
          [`${OV}read`]: { text: "# Deploy guide\n1. ..." },
        },
      });
      await start($, "inline");
      await ask($, "how do we deploy?", A2, [
        { tool: `${OV}search`, query: "deploy" },
        { tool: `${OV}read`, uris: [GUIDE] },
      ]);
      const { text } = await card($, surface, A2, "a2");
      expect(text).toContain("✓ 2 OpenViking sources consulted · 2 team docs · 1 read in full");
      expect(text.indexOf("deploy guide")).toBeLessThan(text.indexOf("incident runbook"));
      expect(text).toContain("⌕ Searched “deploy” · 2 results");
      expect(text).not.toContain("auto-recalled");
    });

    test("4. ov CLI through Bash counts, and the shell command is never stored", async ($: any, on) => {
      const { store } = world(on, {
        recall: null,
        settings: { autoRecall: false },
        tools: { Bash: { text: "# Deploy guide" } },
      });
      await start($, "inline");
      await ask($, "open the deploy guide", A2, [
        { tool: "Bash", command: `OPENVIKING_API_KEY=${KEY} ov read ${GUIDE}` },
      ]);
      const { text } = await card($, surface, A2, "a4");
      expect(text).toContain("✓ 1 OpenViking source consulted");
      expect(text).toContain("deploy guide");
      const saved = JSON.stringify(store);
      expect(saved).toContain("session:sess-1");
      expect(saved).not.toContain(KEY);
      expect(saved).not.toContain("ov read");
    });

    test("4b. a shell command that only mentions a viking:// path is not a lookup", async ($: any, on) => {
      world(on, { recall: null, settings: { autoRecall: false }, tools: { Bash: { text: "ok" } } });
      await start($, "inline");
      const cmd = `python3 - <<'EOF'\nopen('README.md','w').write('see viking://user/…/docs/example.md')\nEOF`;
      await ask($, "update the readme", A2, [
        { tool: "Bash", command: cmd },
        { tool: "Bash", command: `grep -rn "viking://" docs/` },
      ]);
      const { text } = await card($, surface, A2, "a4b");
      expect(text).not.toContain("OpenViking · this answer");
    });

    test("4c. file names with spaces stay whole, from search and grep output", async ($: any, on) => {
      const spaced =
        "viking://user/newuser/memories/events/2026/07/02/2026-07-02 risk report sent.md";
      world(on, {
        recall: null,
        settings: { autoRecall: false },
        tools: {
          [`${OV}search`]: { text: `Found 1 item(s):\n- [memory 57%] ${spaced}\n    summary text` },
          [`${OV}grep`]: {
            text: `Found 1 match(es):\n\n${spaced}\n  L2 [2026-07-\\d\\d]: 2026-07-02 report`,
          },
        },
      });
      await start($, "inline");
      await $.command.run({ command: "openviking-usage", args: "card high" } as never);
      await ask($, "july reports", A2, [
        { tool: `${OV}search`, query: "july" },
        { tool: `${OV}grep`, uri: "viking://user/newuser/memories/events", pattern: ["2026-07"] },
      ]);
      const { text } = await card($, surface, A2, "a4c");
      expect(text).toContain("✓ 1 OpenViking source consulted");
      expect(text).toContain(spaced);
    });

    test("5. a failed lookup is shown as failed and counts nothing", async ($: any, on) => {
      world(on, {
        recall: null,
        settings: { autoRecall: false },
        tools: { [`${OV}search`]: { isError: true, text: "timeout" } },
      });
      await start($, "inline");
      await ask($, "search for the runbook", A2, [{ tool: `${OV}search`, query: "runbook" }]);
      const { text } = await card($, surface, A2, "a5");
      expect(text).not.toContain("sources consulted");
      expect(text).toContain("failed");
    });

    test("6. saving a memory is listed as a write, not as a source", async ($: any, on) => {
      world(on, {
        recall: null,
        settings: { autoRecall: false },
        tools: { [`${OV}remember`]: { text: `Saved to ${NOTE}` } },
      });
      await start($, "inline");
      await ask(
        $,
        "remember that billing owns refunds",
        "Noted. I will keep that in mind for later questions about refunds and invoices.",
        [{ tool: `${OV}remember`, uri: NOTE }],
      );
      const { text } = await card(
        $,
        surface,
        "Noted. I will keep that in mind for later questions about refunds and invoices.",
        "a6",
      );
      expect(text).not.toContain("sources consulted");
      expect(text).toContain("✎ Saved");
    });

    test("7. a health check alone does not produce a card", async ($: any, on) => {
      world(on, {
        recall: null,
        settings: { autoRecall: false },
        tools: { [`${OV}health`]: { text: "ok" } },
      });
      await start($, "inline");
      await ask(
        $,
        "is the server up?",
        "Yes, the server responded normally and everything looks healthy right now.",
        [{ tool: `${OV}health` }],
      );
      const { text } = await card(
        $,
        surface,
        "Yes, the server responded normally and everything looks healthy right now.",
        "a7",
      );
      expect(text).not.toContain("OpenViking · this answer");
    });

    test("8. nothing retrieved: no card, and the sidebar says so", async ($: any, on) => {
      world(on, { recall: null });
      await start($, "pane");
      await ask($, "what is 2 + 2?", "Four.");
      const { text } = await pane($, surface);
      expect(text).toContain("No OpenViking sources for this answer.");
    });

    test("9. a key pasted into the prompt is redacted everywhere", async ($: any, on) => {
      const { store } = world(on, { recall: RECALL });
      await start($, "pane");
      await ask($, `why does ${KEY} fail?`, A1);
      const { text } = await pane($, surface);
      expect(text).not.toContain(KEY);
      expect(text).toContain("[REDACTED]");
      expect(JSON.stringify(store)).not.toContain(KEY);
    });

    test("10. Chinese labels", async ($: any, on) => {
      world(on, { recall: RECALL });
      await start($, "inline");
      await $.command.run({ command: "openviking-usage", args: "lang zh" } as never);
      await ask($, "退款处理怎么写？", A1);
      const { text } = await card($, surface, A1, "a10");
      expect(text).toContain(
        "✓ 参考了 3 个 OpenViking 来源 · 1 你的偏好 · 1 你的工作记忆 · 1 团队文档",
      );
    });

    test("11. two answers ending with the same sentence each keep their own card", async ($: any, on) => {
      world(on, { recall: RECALL });
      await start($, "inline");
      const tail = " Let me know if you want me to go further or adjust anything.";
      const first = `Keep refunds in their own module and validate the invoice first.${tail}`;
      const second = `Put the deploy behind a feature flag and ship it in two waves.${tail}`;
      await ask($, "q1", first);
      await ask($, "q2", second);
      expect((await card($, surface, first, "b1")).text).toContain("OpenViking · this answer");
      expect((await card($, surface, second, "b2")).text).toContain("OpenViking · this answer");
    });

    test("12. an answer in several text blocks: the card sits under the last block", async ($: any, on) => {
      world(on, { recall: RECALL });
      await start($, "inline");
      const blocks = [
        "Looking at the handler and the invoice checks now.",
        "Done: early returns, and the refund call moved last.",
      ];
      await ask($, "q", blocks.join("\n\n"));
      expect((await card($, surface, blocks[1] as string, "c2")).text).toContain(
        "OpenViking · this answer",
      );
      expect((await card($, surface, blocks[0] as string, "c1")).text).not.toContain(
        "OpenViking · this answer",
      );
    });

    test("13. a resumed session gets its earlier answers back", async ($: any, on) => {
      const earlier = {
        n: 1,
        query: "earlier question",
        at: "2026-10-05T01:00:00.000Z",
        latencyMs: null,
        tokensUsed: null,
        items: [
          {
            uri: PREF,
            layer: "L0",
            score: 0.71,
            tokens: 20,
            summary: "Prefers small functions",
            firstTurn: 1,
          },
        ],
        mutedHits: 0,
        cited: [],
        lookups: [],
        reason: "ok",
      };
      world(on, {
        recall: RECALL,
        store: {
          "session:sess-1": {
            lookups: [],
            turn: earlier,
            turnCount: 1,
            seen: [],
            muted: [],
            history: [earlier],
            savedAt: 1,
          },
        },
      });
      await start($, "pane");
      await ask($, "next question", A1);
      const { text } = await pane($, surface);
      expect(text).toContain("“earlier question” ✓1");
    });

    test("14. the first prompt carries the totals card in conversation mode", async ($: any, on) => {
      world(on, { recall: RECALL });
      await start($, "inline");
      await ask($, "how should I structure the refund handler?", A1);
      const ui = await $.ui.mount({
        plugin: "ov-usage",
        surface,
        component: "UserMessage",
        props: {
          text: "how should I structure the refund handler?",
          origin: { kind: "prompt" },
          isExpanded: false,
        },
        requestId: "p1",
      });
      const text = await texts(ui);
      expect(text).toContain("This conversation");
      expect(text).toContain("3 OpenViking sources consulted this session");
    });

    test("15. status line: this answer by group, then the session, on the cards' terms", async ($: any, on) => {
      const { status } = world(on, {
        recall: RECALL,
        tools: { [`${OV}read`]: { text: "# Deploy guide" }, [`${OV}remember`]: { text: "ok" } },
      });
      await start($);
      await ask($, "q1", A1);
      expect(status.at(-1)).toBe("OV · this answer ✓3 (★1 ◆1 ▤1) · 3 this session");
      await ask($, "q2", A2, [
        { tool: `${OV}read`, uris: [GUIDE] },
        { tool: `${OV}remember`, uri: NOTE },
      ]);
      expect(status.at(-1)).toBe("OV · this answer ✓4 (★1 ◆1 ▤2) · 4 this session · 1 saved");
      await $.command.run({ command: "openviking-usage", args: "lang zh" } as never);
      expect(status.at(-1)).toBe("OV · 本次 ✓4 (★1 ◆1 ▤2) · 本会话 4 · 写回 1");
    });

    test("16. status line with auto-recall off and nothing looked up", async ($: any, on) => {
      const { status } = world(on, { recall: RECALL, settings: { autoRecall: false } });
      await start($);
      await ask($, "q", A1);
      expect(status.at(-1)).toBe("OV · no sources for this answer");
    });

    const ctx = (profile: string, files: string[]) =>
      `<openviking-context source="startup">\n<user-profile uri="viking://user/newuser/memories/profile.md">\n# New user\n${profile}\n</user-profile>\n<available-memories>\n  viking://user/newuser/memories/preferences/\n${files.map((f) => `    - ${f}`).join("\n")}\n</available-memories>\n</openviking-context>`;
    const roleCases: [string, string, string[], string][] = [
      [
        "a role line that is not first",
        "- Location: Beijing\n- Role: Product manager",
        ["a.md", "b.md"],
        "Startup: 2 memories indexed · your profile: Product manager",
      ],
      [
        "a long English label",
        "- Occupation: Engineer",
        ["a.md"],
        "Startup: 1 memory indexed · your profile: Engineer",
      ],
      ["no role line at all", "- Location: Beijing", ["a.md"], "Startup: 1 memory indexed"],
      [
        "a brand-new user with no memories yet",
        "- 职业：产品经理（as of 2026-07-08）",
        [],
        "Startup: 0 memories indexed · your profile: 产品经理",
      ],
    ];
    for (const [name, profile, files, expected] of roleCases) {
      test(`17. startup line, ${name}`, async ($: any, on) => {
        world(on, { recall: null, startup: ctx(profile, files) });
        await start($);
        const { text } = await pane($, surface);
        expect(text).toContain(expected);
        expect(text).not.toContain("your profile: Beijing");
      });
    }
  });
}
