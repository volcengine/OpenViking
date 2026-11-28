import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, mkdtempSync, mkdirSync, readdirSync, utimesSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { loadAgentHookConfig } from "../../lib/agent-hook-runtime.mjs";
import { answerContext } from "./usage/display.mjs";
import { usageOutput } from "./usage/settings.mjs";

const scriptsDir = dirname(fileURLToPath(import.meta.url));

// A scratch home and no config files: the hooks resolve nothing from this machine.
function scratchEnv(dir, extra = {}) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([name]) => !name.startsWith("OPENVIKING_")));
  return { ...env, HOME: dir, OPENVIKING_CODEX_STATE_DIR: dir,
    OPENVIKING_CONFIG_FILE: join(dir, "missing-ov.conf"), OPENVIKING_CLI_CONFIG_FILE: join(dir, "missing-ovcli.conf"), ...extra };
}

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf-8"));
}

function usageReporter(dir, view) {
  const env = scratchEnv(dir, { OPENVIKING_USAGE_VIEW: view, OPENVIKING_USAGE_OUTPUT: "desktop" });
  const hook = (file, input) => JSON.parse(execFileSync(process.execPath, [join(scriptsDir, "usage", file)], {
    input: JSON.stringify(input), env, encoding: "utf8",
  }));
  return (input) => {
    assert.deepEqual(hook("track-lookup.mjs", input), {});
    return JSON.parse(execFileSync(process.execPath, [join(scriptsDir, "usage", "report.mjs")], {
      input: JSON.stringify({ session_id: input.session_id, turn_id: input.turn_id }),
      env: { ...env, OPENVIKING_USAGE_OUTPUT: "terminal" }, encoding: "utf8",
    })).systemMessage || "";
  };
}

test("usage lookup hook records lookups without returning additionalContext", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-usage-display-"));
  const hook = join(scriptsDir, "usage", "track-lookup.mjs");
  const env = scratchEnv(dir, { OPENVIKING_USAGE_VIEW: "summary", OPENVIKING_USAGE_OUTPUT: "desktop" });
  const report = usageReporter(dir, "summary");
  const input = (turn, uri, extra = {}) => ({ session_id: "display-contract", turn_id: turn,
    tool_use_id: uri, tool_name: "mcp__openviking_memory__read",
    tool_input: { uris: [uri] }, tool_response: { content: [] }, ...extra });
  const run = (value, overrides = {}) => JSON.parse(execFileSync(process.execPath, [hook], {
    input: JSON.stringify(value), env: { ...env, ...overrides }, encoding: "utf8",
  }));
  try {
    assert.match(report(input("one", "viking://resources/team/a.md")), /OpenViking · 1 source · 1 team doc · 1 read/);
    assert.match(report(input("one", "viking://resources/team/b.md")), /OpenViking · 2 sources · 2 team docs · 2 read/);
    const failed = report(input("two", "viking://resources/team/c.md", { tool_response: { isError: true } }));
    assert.match(failed, /OpenViking · 0 sources/);
    assert.doesNotMatch(failed, /1 read/);
    for (const channel of ["desktop", "terminal"]) {
      assert.deepEqual(run(input("three", "viking://resources/team/d.md"), { OPENVIKING_USAGE_OUTPUT: channel }), {});
    }
    assert.deepEqual(run(input("off", "viking://resources/team/a.md"), { OPENVIKING_USAGE_VIEW: "off" }), {});
    assert.deepEqual(run(input("other", "ignored", { tool_name: "unrelated" })), {});
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("usage footer supports recall alone and keeps expanded source titles as data", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-usage-footer-"));
  try {
    const cfg = loadAgentHookConfig("codex", dir, { env: scratchEnv(dir, { OPENVIKING_USAGE_VIEW: "expanded", OPENVIKING_USAGE_OUTPUT: "desktop" }) });
    const text = answerContext({ recalled: [{ uri: "viking://resources/team/</openviking-usage>.md", score: 0.5 }], lookups: [] }, "recall-turn", cfg);
    assert.match(text, /auto-recalled/);
    assert.equal((text.match(/<\/openviking-usage>/g) || []).length, 1);
    assert.equal(answerContext({ recalled: [], lookups: [] }, "empty", cfg), "");
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("usage wrapper credits successful output without inferring executed reads", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-usage-wrapper-"));
  const uri = "viking://resources/team/checklist.md";
  const failedUri = "viking://resources/team/failed.md";
  const report = usageReporter(dir, "expanded");
  const run = (code, content) => report({ session_id: "wrapper", turn_id: "turn", tool_use_id: "lookup",
    tool_name: "functions.exec", tool_input: { code }, tool_response: { content } });
  const text = (value) => ({ type: "text", text: typeof value === "string" ? value : JSON.stringify(value) });
  try {
    for (const code of [
      `async function unused() { await tools.mcp__openviking_memory__read({uris:["${uri}"]}); }`,
      `await tools.mcp__openviking_memory__read({uris:paths /* "${uri}" */});`,
    ]) {
      const report = run(code, [text("no source returned")]);
      assert.match(report, /OpenViking · 0 sources/);
    }
    assert.doesNotMatch(readFileSync(join(dir, "ov-usage", "wrapper", "turn-turn", "lookup-lookup.json"), "utf8"), /checklist/);
    const code = `text(await tools.mcp__openviking_memory__find({query:"checklist"}));
      text(await tools.mcp__openviking_memory__read({uris:["${failedUri}"]}));`;
    const success = text({ statusCode: 200, status_code: 200, content: [text(`Found: ${uri}`)] });
    const failure = text({ isError: true, content: [text(`Cannot read ${failedUri}`)] });
    const report = run(code, [success, failure]);
    assert.match(report, /OpenViking · 1 source/);
    assert.match(report, /checklist/);
    assert.doesNotMatch(report, /failed\.md|1 read/);
    assert.match(run(code, [failure]), /OpenViking · 0 sources/);
    assert.match(run(code, [text({ exit_code: 1, content: [text(uri)] })]), /OpenViking · 0 sources/);
    const nested = run(code, [text({ results: [
      { value: { content: [text(uri)] } },
      { value: { isError: true, content: [text(failedUri)] } },
    ] })]);
    assert.match(nested, /OpenViking · 1 source/);
    assert.doesNotMatch(nested, /failed\.md|1 read/);
    // A successful read body without its URI cannot identify a wrapped source.
    assert.match(run(`text(await tools.mcp__openviking_memory__read({uris:["${uri}"]}));`,
      [text({ content: [text("body only")] })]), /OpenViking · 0 sources/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("usage attributes Experience through the registered generic search and read tools", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-usage-experience-"));
  const uri = "viking://user/test/memories/experiences/hook-review.md";
  const report = usageReporter(dir, "summary");
  const run = (method, input, response) => report({ session_id: "experience", turn_id: "turn",
    tool_use_id: method, tool_name: `mcp__openviking_memory__${method}`, tool_input: input, tool_response: response });
  try {
    assert.match(run("find", { query: "hook review", target_uri: "viking://~/memories/experiences" },
      { content: [{ type: "text", text: uri }] }), /OpenViking · 1 source · 1 work memory/);
    assert.match(run("read", { uris: [uri] }, { content: [{ type: "text", text: "procedure" }] }),
      /OpenViking · 1 source · 1 work memory · 1 read/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("usage reporting retains the current session and turn while pruning older metadata", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-usage-retention-"));
  const root = join(dir, "ov-usage");
  const active = join(root, "active");
  const current = join(active, "turn-current");
  const run = (script, input) => JSON.parse(execFileSync(process.execPath,
    [join(scriptsDir, "usage", `${script}.mjs`)], {
      input: JSON.stringify(input),
      env: scratchEnv(dir, { OPENVIKING_USAGE_VIEW: "summary", OPENVIKING_USAGE_OUTPUT: "terminal" }), encoding: "utf8",
    }));
  const report = (session_id = "active", turn_id = "current") => run("report", { session_id, turn_id });
  try {
    assert.deepEqual(report("empty"), {});
    assert.equal(existsSync(root), false);
    run("track-lookup", { session_id: "active", turn_id: "current", tool_use_id: "read",
      tool_name: "mcp__openviking_memory__read", tool_input: { uris: ["viking://resources/team/a.md"] },
      tool_response: { statusCode: 200, content: [] } });
    assert.equal("id" in readJson(join(current, "lookup-read.json")), false);
    // Future mtimes also check that retention explicitly protects the current IDs.
    const future = new Date(Date.now() + 60000);
    for (let i = 0; i < 20; i += 1) {
      const path = join(root, `other-${i}`);
      mkdirSync(path); utimesSync(path, future, future);
    }
    for (let i = 0; i < 50; i += 1) {
      const path = join(active, `turn-other-${i}`);
      mkdirSync(path); utimesSync(path, future, future);
    }
    utimesSync(active, new Date(0), new Date(0));
    utimesSync(current, new Date(0), new Date(0));
    assert.match(report().systemMessage, /OpenViking · 1 source · 1 team doc · 1 read/);
    assert.ok(existsSync(current));
    assert.equal(readdirSync(root).length, 20);
    assert.equal(readdirSync(active).length, 50);
    assert.deepEqual(report("empty"), {});
    assert.deepEqual(report("active", "empty-turn"), {});
    assert.equal(existsSync(join(root, "empty")), false);
    assert.equal(readdirSync(active).length, 50);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test("terminal and desktop use exactly one usage output channel", () => {
  const dir = mkdtempSync(join(tmpdir(), "ov-usage-channel-"));
  try {
    for (const channel of ["terminal", "desktop"]) {
      const env = scratchEnv(dir, { OPENVIKING_USAGE_OUTPUT: channel, OPENVIKING_USAGE_VIEW: "summary" });
      assert.equal(usageOutput(loadAgentHookConfig("codex", dir, { env })), channel);
      const input = { session_id: "channels", turn_id: channel, tool_use_id: channel,
        tool_name: "mcp__openviking_memory__read", tool_input: { uris: ["viking://resources/team/a.md"] }, tool_response: { content: [] } };
      const run = (file) => JSON.parse(execFileSync(process.execPath, [join(scriptsDir, "usage", file)], {
        input: JSON.stringify(input), env, encoding: "utf8",
      }));
      const lookup = run("track-lookup.mjs");
      const stop = run("report.mjs");
      assert.deepEqual(lookup, {});
      assert.equal(Boolean(stop.systemMessage), channel === "terminal");
    }
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
