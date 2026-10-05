import { describe, expect, mock, test } from "claude-code/testing";
import type { On } from "claude-code";

const HOME = "/home/tester";
const PRD = "viking://user/tester/memories/entities/项目文档/sample_feature_prd.md";
const ACL = "viking://resources/wiki/concept/resource-access-control.md";
const PREF = "viking://user/tester/memories/preferences/me/按钮状态设计偏好.md";
const SEARCH = "mcp__plugin_openviking-memory_openviking__search";
const READ = "mcp__plugin_openviking-memory_openviking__read";

const RECALL = `<openviking-context>
<memory uri="${PREF}" type="preferences" score="0.62" detail="abstract">
- 不可用的操作，偏好把入口置灰并说明原因
</memory>
</openviking-context>`;

// The engine beneath the plugin, with recall answering `recall` (nothing when null).
function world(on: On, recall: string | null) {
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
    [`${HOME}/.openviking/state/last-recall.json`]: JSON.stringify({
      reason: recall ? "ok" : "no_results",
      cc_session_id: "sess-1",
    }),
  };
  mock.env(on, { HOME });
  mock.store(on);
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
  on("ui.open", () => ({ value: undefined }) as never);
  on("ui.status", () => ({ value: undefined }) as never);
  on("ui.toast", () => ({ value: undefined }) as never);
  on("session.start", () => ({ cwd: "/work" }) as never);
  on("turn.complete", (_$, e) => ({ text: e.answer }));
  on("classic.UserPromptSubmit", () => ({ additionalContext: recall ? [recall] : undefined }));
  // A search returns both docs; a read returns the file's text.
  on(
    "tool.call",
    (_$, e) =>
      (e.tool === SEARCH
        ? { result: "ok", text: `Found 2 item(s):\n- ${PRD}\n- ${ACL}` }
        : { result: "ok", text: "# file body" }) as never,
  );
}

async function answer($: any, text: string) {
  await $.turn.complete({
    answer: text,
    durationMs: 10,
    isAborted: false,
    turnId: "t",
    reason: "answer",
  } as never);
}

async function pane($: any, surface: "terminal" | "desktop", opts: { details?: boolean } = {}) {
  const ui = await $.ui.mount({
    plugin: "ov-usage",
    surface,
    component: "Pane",
    props: { title: "OV-Usage", isFocused: true, bodyColumns: 80 },
    requestId: "ov-usage",
  });
  if (opts.details) await ui.press({ key: "toggle-details" });
  const texts = await ui.findAll({ type: "Text" });
  return texts.map((t: { text: string }) => t.text).join("\n");
}

for (const surface of ["terminal", "desktop"] as const) {
  describe(surface, () => {
    test("with no auto-recall, Claude’s own search and read still show as what the answer referred to", async ($: any, on) => {
      world(on, null);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await $.classic.UserPromptSubmit({
        prompt: "what is the status of the permission model",
        session_id: "sess-1",
      } as never);
      await $.tool.call({
        tool: SEARCH,
        tool_use_id: "a",
        query: "enterprise permission model",
      } as never);
      await $.tool.call({ tool: READ, tool_use_id: "b", uris: [PRD] } as never);
      await answer($, `Sources consulted:\n- ${PRD}`);
      const text = await pane($, surface);

      // recall gave nothing, so by default it is not shown; [details] says why
      expect(text).not.toContain("Auto-recalled");
      expect(text).toContain("Claude's own lookups");
      expect(text).toContain("“enterprise permission model”");
      expect(text).toContain("2 results");
      expect(text).toContain("sample feature prd");
      // the cited doc came from the lookup, and is shown as used
      // everything the search and the read returned counts as consulted
      expect(text).toContain("✓ 2 OpenViking sources consulted");
      expect(text).toContain("found by Claude");
      expect(text).toContain("resource access control");
    });

    test("a source read in an earlier turn is not counted again for a later answer", async ($: any, on) => {
      world(on, null);
      await $.session.start({ source: "startup" } as never);
      await $.classic.UserPromptSubmit({ prompt: "first question", session_id: "sess-1" } as never);
      await $.tool.call({ tool: READ, tool_use_id: "e2", uris: [PRD] } as never);
      await answer($, "first answer");
      await $.classic.UserPromptSubmit({
        prompt: "second question",
        session_id: "sess-1",
      } as never);
      // even naming it in the answer does not count: only what was retrieved for this answer does
      await answer($, `Reused from earlier: ${PRD}`);
      const text = await pane($, surface);
      expect(text).toContain("No OpenViking sources for this answer.");
      expect(text).toContain("“first question” ✓1");
    });

    test("[details] shows why recall gave nothing", async ($: any, on) => {
      world(on, null);
      await $.session.start({ source: "startup" } as never);
      await $.classic.UserPromptSubmit({
        prompt: "what is the status of the permission model",
        session_id: "sess-1",
      } as never);
      await answer($, "no sources");
      const text = await pane($, surface, { details: true });
      expect(text).toContain("No memory scored above the relevance threshold");
    });

    test("auto-recall and lookups show side by side", async ($: any, on) => {
      world(on, RECALL);
      await $.session.start({ source: "startup", cwd: "/work" } as never);
      await $.classic.UserPromptSubmit({
        prompt: "how should restricted actions look",
        session_id: "sess-1",
      } as never);
      await $.tool.call({ tool: READ, tool_use_id: "c", uris: [ACL] } as never);
      await answer($, "入口置灰，按你的偏好（按钮状态设计偏好）。");
      const text = await pane($, surface);

      expect(text).toContain("✓ 2 OpenViking sources consulted");
      expect(text).toContain("按钮状态设计偏好");
      expect(text).toContain("Claude's own lookups");
      expect(text).toContain("resource access control");
      expect(text).not.toContain("No memory scored above the relevance threshold");
    });
  });
}
