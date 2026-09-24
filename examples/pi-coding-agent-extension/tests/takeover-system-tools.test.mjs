import test from "node:test";
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  OVERVIEW_MARKER,
  TAKEOVER_ENTRY_TYPE,
  TakeoverCore,
} from "../lib/takeover-core.mjs";

const EXTENSION_DIR = dirname(fileURLToPath(import.meta.url));

/**
 * pi's real transcript helpers, wherever pi is installed. The stable extension
 * does not depend on pi (only `@modelcontextprotocol/client`), so this resolves
 * against a local install, the global homebrew prefix, or an explicit override.
 * When none is found the tool-declaration checks skip rather than testing a
 * reimplementation of pi's own merge — the pure ordering checks in
 * takeover-core.test.mjs still run everywhere.
 *
 * pi < 0.86 has no `system` role and ships no `utils/transcript.js`, so this
 * also skips there, which is correct: there are no covered system messages to
 * preserve on 0.80.3.
 */
function findTranscriptModule() {
  // An explicit override is also used by the version-matrix gate. Do not fall
  // back to a different globally installed pi when that version has no helper.
  if (process.env.PI_PI_AI_TRANSCRIPT !== undefined) {
    const explicit = process.env.PI_PI_AI_TRANSCRIPT;
    return explicit && existsSync(explicit) ? explicit : "";
  }
  const candidates = [];
  for (const base of [
    "@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/utils/transcript.js",
    "@earendil-works/pi-ai/dist/utils/transcript.js",
  ]) {
    try {
      candidates.push(createRequire(join(EXTENSION_DIR, "package.json")).resolve(base));
    } catch {
      // not installed at this location
    }
  }
  candidates.push(
    "/opt/homebrew/lib/node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/utils/transcript.js",
    "/usr/local/lib/node_modules/@earendil-works/pi-coding-agent/node_modules/@earendil-works/pi-ai/dist/utils/transcript.js",
  );
  return candidates.find((path) => path && existsSync(path)) ?? "";
}

const TRANSCRIPT_PATH = findTranscriptModule();

/**
 * Run the takeover cut over `messages` as a pi >= 0.86 branch (every message,
 * system ones included, is a `message` entry), with the covered prefix ending
 * right before the user turn `keptText`. Asserts the cut really happened, so a
 * passing tool check can never come from an untrimmed context.
 */
function cut(messages, keptText) {
  const branch = messages.map((message, i) => ({ id: `e${i}`, type: "message", message }));
  const kept = messages.findIndex((message) => message.role === "user" && message.content === keptText);
  const out = makeCore(branch[kept - 1].id).transformContext(messages, branch);
  assert.ok(out.some((message) => String(message.content).startsWith(OVERVIEW_MARKER)));
  assert.ok(out.length < messages.length);
  return out;
}

function makeCore(coveredThroughEntryId, overrides = {}) {
  const core = new TakeoverCore({
    config: {
      takeoverEnabled: true,
      takeoverTokenThreshold: 100,
      takeoverKeepRecentTurns: 1,
      takeoverOverviewBudget: 1000,
      takeoverOverviewPollMs: 1,
      takeoverOverviewPollMax: 3,
      ...overrides,
    },
  });
  core.restore([
    {
      type: "custom",
      customType: TAKEOVER_ENTRY_TYPE,
      data: { coveredThroughEntryId, coveredUserTurns: 1, overview: "archived first turn", pendingTokens: 0 },
    },
  ]);
  return core;
}

function sys(content, extra = {}) {
  return { role: "system", content, timestamp: 0, ...extra };
}
let clock = 0;
function user(content) {
  return { role: "user", content, timestamp: ++clock };
}
function assistant(content) {
  return { role: "assistant", content, timestamp: ++clock };
}

test("transformContext preserves tool declarations, replacements, sections and appended instructions", async (t) => {
  if (!TRANSCRIPT_PATH) {
    t.skip("pi @earendil-works/pi-ai transcript helpers not resolvable (skips on pi < 0.86)");
    return;
  }
  const { getCurrentSystemMessage, getCurrentSystemPrompt, getCurrentTools } = await import(TRANSCRIPT_PATH);

  const readTool = { name: "read", description: "read a file", parameters: { type: "object" } };
  const searchTool = { name: "openviking_search", description: "search", parameters: { type: "object" } };
  const updatedReadTool = { name: "read", description: "read safely", parameters: { type: "object" } };

  const messages = [
    // The leading system message: base prompt + the initial tools, exactly the
    // shape @earendil-works/pi-ai's normalizeContext folds Context into.
    sys("BASE PROMPT", { toolsAdded: [readTool], sections: { style: "be terse", obsolete: "drop me" } }),
    user("first"),
    assistant("answer"),
    // A mid-conversation tool addition and a section update, both in the covered
    // region — this is what a naive slice would strip.
    sys("APPENDED INSTRUCTION", {
      toolsRemoved: [{ name: "read" }],
      toolsAdded: [updatedReadTool, searchTool],
      sections: { style: "be exact", obsolete: null, extra: "note" },
    }),
    user("second"),
    assistant("answer 2"),
  ];

  const before = new Set(getCurrentTools(messages).map((tt) => tt.name));
  assert.deepEqual([...before].sort(), ["openviking_search", "read"]);

  const out = cut(messages, "second");
  const after = new Set(getCurrentTools(out).map((tt) => tt.name));

  // The model's tools are unchanged by the takeover cut.
  assert.deepEqual([...after].sort(), [...before].sort());
  assert.equal(getCurrentTools(out).find((tool) => tool.name === "read").description, "read safely");

  // The base prompt and both section updates survive the merge, too.
  const prompt = getCurrentSystemPrompt(out);
  assert.match(prompt, /BASE PROMPT/);
  assert.match(prompt, /APPENDED INSTRUCTION/);
  const systemState = getCurrentSystemMessage(out);
  assert.deepEqual(systemState.sections, { style: "be exact", extra: "note" });
  assert.equal(out.filter((message) => message.role === "system").length, 2);
});

test("transformContext preserves a mid-conversation tool removal", async (t) => {
  if (!TRANSCRIPT_PATH) {
    t.skip("pi @earendil-works/pi-ai transcript helpers not resolvable (skips on pi < 0.86)");
    return;
  }
  const { getCurrentTools } = await import(TRANSCRIPT_PATH);

  const readTool = { name: "read", description: "read", parameters: { type: "object" } };
  const writeTool = { name: "write", description: "write", parameters: { type: "object" } };

  const messages = [
    sys("BASE", { toolsAdded: [readTool, writeTool] }),
    user("first"),
    assistant("answer"),
    // `write` is removed mid-conversation, in the covered region.
    sys("", { toolsRemoved: [{ name: "write" }] }),
    user("second"),
    assistant("answer 2"),
  ];

  assert.deepEqual(getCurrentTools(messages).map((tt) => tt.name), ["read"]);
  const out = cut(messages, "second");
  // Without preserving the removal, `write` would come back to life.
  assert.deepEqual(getCurrentTools(out).map((tt) => tt.name), ["read"]);
});

test("dropping covered system messages loses tools (regression witness)", async (t) => {
  if (!TRANSCRIPT_PATH) {
    t.skip("pi @earendil-works/pi-ai transcript helpers not resolvable (skips on pi < 0.86)");
    return;
  }
  const { getCurrentTools } = await import(TRANSCRIPT_PATH);
  const readTool = { name: "read", description: "read", parameters: { type: "object" } };
  const messages = [
    sys("BASE", { toolsAdded: [readTool] }),
    user("first"),
    assistant("answer"),
    user("second"),
  ];
  // What the pre-fix slice produced: covered system messages gone entirely.
  const naive = messages.slice(3);
  assert.deepEqual(getCurrentTools(naive), []);
  // The fix keeps them, so pi still sees the tool.
  const out = cut(messages, "second");
  assert.deepEqual(getCurrentTools(out).map((tt) => tt.name), ["read"]);
});
