#!/usr/bin/env node
/**
 * Live e2e gate for the agent-driven context window.
 *
 * Manual by design: it drives a real pi binary, a real OpenViking server and a
 * real OpenAI-compatible LLM relay, and asserts the whole reset pipeline —
 * archive to OpenViking, virtual cut of the provider payload, frozen window
 * header, cross-process restore, fail-closed refusal.
 *
 * Scenario (plan "端到端"):
 *   T1 `pi -p`   remember ZEPHYR-9942 + the deploy window, padded with PAD1.
 *   T2 `pi -c`   instruct the model to call new_context alone with a fixed
 *                reason and notes carrying the codename and the next step,
 *                then reply READY in the new window.
 *   T3 `pi -c`   ask for the codename (a fresh process, so the window state
 *                has to survive in the session file).
 *
 * Required env:
 *   OPENVIKING_URL
 *   OPENVIKING_API_KEY
 *   E2E_LLM_API_KEY          LLM provider API key (never written to disk)
 *
 * Optional env:
 *   PI_BIN                   path to the pi binary; defaults to `which pi`
 *   E2E_LLM_BASE_URL         base URL of an OpenAI- or Anthropic-compatible
 *                            endpoint; required, no default, so a private relay
 *                            never ends up hardcoded here
 *   E2E_LLM_MODEL            model id served by that endpoint; required
 *   E2E_LLM_API              pi provider api type; default openai-completions
 *   E2E_LLM_REASONING        thinking level for the model: off (default),
 *                            minimal, low, medium, high or xhigh. Anything but
 *                            off marks the model as reasoning-capable and sets
 *                            pi's defaultThinkingLevel, so an OpenAI-compatible
 *                            relay receives reasoning_effort.
 *   E2E_KEEP_TMP=1           keep the temp workspaces even on success
 *   E2E_KEEP_OV_SESSION=1    do not delete the OpenViking sessions afterwards,
 *                            so the archives stay readable on the server (for
 *                            demos; the sessions are yours to clean up)
 *   E2E_WINDOW_LONG=1        run ONLY the long-context scenario: the agent
 *                            inventories a real code tree until context
 *                            pressure makes it reset on its own, with no
 *                            instruction to call the tool. `=both` adds it to
 *                            the other scenarios. Slow (10-20 min) and it
 *                            depends on model judgement, so the judgement
 *                            checks warn rather than fail.
 *   E2E_WINDOW_FAILCLOSED=1  run ONLY the fail-closed variant: OPENVIKING_URL
 *                            is pointed at a closed port and the gate asserts
 *                            that nothing was cut. `=both` runs both scenarios.
 *
 * Exit codes: 0 all checks passed, 1 a check failed, 2 bad environment.
 */
import { spawnSync } from "node:child_process";
import {
  copyFileSync,
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  statSync,
  rmSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// The gate asserts on the extension's own markers, so it imports them instead
// of copying the strings: a rename in the core has to break the gate loudly.
import {
  HANDOFF_MARKER,
  STATUS_CUSTOM_TYPE,
  WINDOW_ENTRY_TYPE,
  WINDOW_HEADER_OPEN,
} from "../lib/context-window-core.mjs";

const EXT_SRC = dirname(dirname(fileURLToPath(import.meta.url)));

const OV_URL = process.env.OPENVIKING_URL;
const OV_KEY = process.env.OPENVIKING_API_KEY;
const LLM_KEY = process.env.E2E_LLM_API_KEY;
const LLM_BASE = process.env.E2E_LLM_BASE_URL;
const LLM_MODEL = process.env.E2E_LLM_MODEL;
const LLM_API = process.env.E2E_LLM_API ?? "openai-completions";
const THINKING_LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh"];
const LLM_REASONING = (process.env.E2E_LLM_REASONING ?? "off").trim() || "off";
const KEEP_OV_SESSION = (process.env.E2E_KEEP_OV_SESSION ?? "") === "1";
const FAILCLOSED_MODE = (process.env.E2E_WINDOW_FAILCLOSED ?? "").trim();
const LONG_MODE = (process.env.E2E_WINDOW_LONG ?? "").trim();
const RUN_FAILCLOSED = FAILCLOSED_MODE === "1" || FAILCLOSED_MODE === "both";
const RUN_LONG = LONG_MODE === "1" || LONG_MODE === "both";
const RUN_MAIN = FAILCLOSED_MODE !== "1" && LONG_MODE !== "1";
/** Context share the long scenario must reach before the agent resets. */
const LONG_MIN_PERCENT = Number(process.env.E2E_WINDOW_LONG_MIN_PERCENT ?? 40);
/** Where the soft reminder fires: high enough that the run is a real workload. */
const LONG_SOFT_PERCENT = Number(process.env.E2E_WINDOW_LONG_SOFT_PERCENT ?? 45);
/** What models.json tells pi the window is; used to turn payload size into a share. */
const LONG_CONTEXT_WINDOW = 128000;

/** A port nothing listens on: the fail-closed variant's "OpenViking is down". */
const DEAD_OV_URL = "http://127.0.0.1:9";

const PROVIDER_ID = "e2e-relay";
const RESET_TOOL = "new_context";
const CODENAME = "ZEPHYR-9942";
const DEPLOY_WINDOW = "Friday 03:00 UTC";
const RELEASE_FILE = "release.md";
/** Only present in release.md, so answering with it proves the file was read. */
const RELEASE_OWNER = "dana-okonkwo";
const RESET_REASON = "phase one complete, starting phase two";
/** The part of the reason we require verbatim in the archive and the entry. */
const REASON_MARKER = "phase one complete";
const NEXT_STEP_MARKER = "rollout checklist";
const PAD1 = `PADDING-T1 ${"lorem ipsum dolor sit amet consectetur ".repeat(60)}`;

// ============================================================================
// Environment validation (runs before anything is created, so a bad env is a
// clean exit 2 and the script stays dry-runnable).
// ============================================================================

function which(cmd) {
  const res = spawnSync("which", [cmd], { encoding: "utf8" });
  return res.status === 0 ? res.stdout.trim() : "";
}

const PI_BIN = process.env.PI_BIN || which("pi");

for (const [key, value] of [
  ["OPENVIKING_URL", OV_URL],
  ["OPENVIKING_API_KEY", OV_KEY],
  ["E2E_LLM_API_KEY", LLM_KEY],
  ["E2E_LLM_BASE_URL", LLM_BASE],
  ["E2E_LLM_MODEL", LLM_MODEL],
  ["PI_BIN or pi on PATH", PI_BIN],
]) {
  if (!value) {
    console.error(`e2e-window: missing required ${key}`);
    process.exit(2);
  }
}
if (!existsSync(PI_BIN)) {
  console.error(`e2e-window: pi binary not found: ${PI_BIN}`);
  process.exit(2);
}
if (!THINKING_LEVELS.includes(LLM_REASONING)) {
  console.error(
    `e2e-window: E2E_LLM_REASONING must be one of ${THINKING_LEVELS.join(", ")}, got "${LLM_REASONING}"`,
  );
  process.exit(2);
}
if (LONG_MODE && !["1", "both"].includes(LONG_MODE)) {
  console.error(`e2e-window: E2E_WINDOW_LONG must be "1" or "both", got "${LONG_MODE}"`);
  process.exit(2);
}
if (FAILCLOSED_MODE && !["1", "both"].includes(FAILCLOSED_MODE)) {
  console.error(`e2e-window: E2E_WINDOW_FAILCLOSED must be "1" or "both", got "${FAILCLOSED_MODE}"`);
  process.exit(2);
}

// ============================================================================
// Reporting
// ============================================================================

let failures = 0;
let warnings = 0;
const pass = (msg) => console.log(`  PASS  ${msg}`);
const fail = (msg) => {
  failures++;
  console.error(`  FAIL  ${msg}`);
};
const warn = (msg) => {
  warnings++;
  console.log(`  WARN  ${msg}`);
};
const check = (cond, msg) => (cond ? pass(msg) : fail(msg));
const section = (title) => console.log(`\ne2e-window: --- ${title} ---`);

// ============================================================================
// Workspace
// ============================================================================

/**
 * A private pi installation: its own agent dir (models.json + settings.json),
 * its own copy of the extension, its own session dir and project cwd. Nothing
 * touches the user's ~/.pi.
 */
function makeWorkspace(label, { windowConfig = {}, seedSourceTree = false } = {}) {
  const root = mkdtempSync(join(tmpdir(), `ov-pi-window-${label}-`));
  const ws = {
    label,
    root,
    agentDir: join(root, "agent"),
    // Deliberately outside the agent dir: pi is told to load it with `-e`, and
    // a copy under <agentDir>/extensions could be picked up a second time.
    extDir: join(root, "ext"),
    outDir: join(root, "out"),
    projDir: join(root, "proj"),
    sessionDir: join(root, "sessions"),
  };
  for (const dir of [ws.agentDir, ws.extDir, ws.outDir, ws.projDir, ws.sessionDir]) {
    mkdirSync(dir, { recursive: true });
  }

  writeFileSync(
    join(ws.agentDir, "models.json"),
    JSON.stringify(
      {
        providers: {
          [PROVIDER_ID]: {
            name: "OpenViking e2e relay",
            baseUrl: LLM_BASE,
            api: LLM_API,
            apiKey: "$E2E_LLM_API_KEY",
            ...(LLM_API === "openai-completions"
              ? { authHeader: true, headers: { "x-session-id": "ov-pi-e2e-window" } }
              : {}),
            models: [
              {
                id: LLM_MODEL,
                name: "OpenViking e2e model",
                reasoning: LLM_REASONING !== "off",
                // A custom relay is not recognised by pi-ai's URL-based
                // auto-detection, so reasoning_effort has to be enabled here.
                ...(LLM_REASONING !== "off" && LLM_API === "openai-completions"
                  ? { compat: { supportsReasoningEffort: true, thinkingFormat: "openai" } }
                  : {}),
                input: ["text"],
                cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
                contextWindow: 128000,
                maxTokens: 8192,
              },
            ],
          },
        },
      },
      null,
      2,
    ),
  );
  writeFileSync(
    join(ws.agentDir, "settings.json"),
    JSON.stringify(
      {
        defaultProjectTrust: "always",
        ...(LLM_REASONING !== "off" ? { defaultThinkingLevel: LLM_REASONING } : {}),
      },
      null,
      2,
    ),
  );

  // The extension itself: sources plus lib/shared/scripts, with a config that
  // is generous about the archive deadline (phase 2 takes 25-55s on the target
  // server) and eager about the reminders, so a short gate still exercises the
  // thresholds.
  for (const name of readdirSync(EXT_SRC)) {
    if (name === "config.json") continue;
    const src = join(EXT_SRC, name);
    const dst = join(ws.extDir, basename(name));
    if (name.endsWith(".ts") || name === "README.md") {
      copyFileSync(src, dst);
    } else if (["lib", "shared", "scripts"].includes(name)) {
      cpSync(src, dst, { recursive: true });
    }
  }
  writeFileSync(
    join(ws.extDir, "config.json"),
    JSON.stringify(
      {
        enabled: true,
        syncTurns: true,
        logLevel: "info",
        contextWindow: {
          resetDeadlineMs: 120000,
          archivePollMs: 3000,
          softPercent: 60,
          hardPercent: 75,
          statusEveryTurn: true,
          ...windowConfig,
        },
      },
      null,
      2,
    ),
  );

  // The long scenario needs enough real material that reading it through does
  // not fit in one window. The extension's own sources are the closest thing to
  // hand: about 60k tokens of TypeScript and ESM, already in the repo.
  if (seedSourceTree) {
    const srcDir = join(ws.projDir, "src");
    mkdirSync(srcDir, { recursive: true });
    let bytes = 0;
    for (const name of readdirSync(EXT_SRC)) {
      const from = join(EXT_SRC, name);
      if (name.endsWith(".ts")) {
        copyFileSync(from, join(srcDir, name));
        bytes += statSync(from).size;
      } else if (name === "lib" || name === "tests") {
        for (const child of readdirSync(from)) {
          if (!child.endsWith(".mjs")) continue;
          copyFileSync(join(from, child), join(srcDir, child));
          bytes += statSync(join(from, child)).size;
        }
      }
    }
    const files = readdirSync(srcDir).sort();
    writeFileSync(
      join(ws.projDir, "TASK.md"),
      [
        "# Source inventory task",
        "",
        `There are ${files.length} files under src/ (~${Math.round(bytes / 1024)} KB).`,
        "Work through them in alphabetical order:",
        "",
        ...files.map((f) => `- [ ] src/${f}`),
        "",
      ].join("\n"),
    );
    console.log(
      `e2e-window: [${label}] seeded ${files.length} source files, ` +
        `${Math.round(bytes / 1024)} KB (~${Math.round(bytes / 4000)}k tokens if fully read)`,
    );
  }

  // A file for T1 to read: the gate asserts that tool OUTPUT reaches the
  // archive, so the first turn must produce a tool result rather than relying
  // on the model deciding to use a tool on its own.
  writeFileSync(
    join(ws.projDir, RELEASE_FILE),
    [
      "# Release checklist",
      "",
      `- codename: ${CODENAME}`,
      `- deploy window: ${DEPLOY_WINDOW}`,
      `- owner: ${RELEASE_OWNER}`,
      "- status: phase one in progress",
      "",
    ].join("\n"),
  );

  console.log(`e2e-window: [${label}] workspace ${root}`);
  return ws;
}

/** Run one pi turn in a workspace and capture stdout/stderr. */
function runTurn(ws, turn, prompt, { continueSession = false, ovUrl = OV_URL, timeoutMs = 600_000 } = {}) {
  console.log(`\ne2e-window: [${ws.label}] --- turn ${turn} ---`);
  const args = [
    "--provider", PROVIDER_ID,
    "--model", LLM_MODEL,
    "--mode", "text",
    "-p",
    "-ne",
    "-e", join(ws.extDir, "index.ts"),
    "-e", join(ws.extDir, "scripts", "e2e-probe.ts"),
    "--approve",
    "--no-context-files",
    "--no-skills",
    "--no-prompt-templates",
    "--session-dir", ws.sessionDir,
    "--offline",
    ...(continueSession ? ["-c"] : []),
    prompt,
  ];
  const res = spawnSync(PI_BIN, args, {
    cwd: ws.projDir,
    env: {
      ...process.env,
      PI_CODING_AGENT_DIR: ws.agentDir,
      OPENVIKING_URL: ovUrl,
      OPENVIKING_API_KEY: OV_KEY,
      E2E_LLM_API_KEY: LLM_KEY,
      OV_E2E_OUT: ws.outDir,
      OV_E2E_TURN: String(turn),
      OPENVIKING_DEBUG_LOG: join(ws.outDir, "ov-pi.log"),
    },
    timeout: timeoutMs,
    encoding: "utf8",
  });
  const out = `${res.stdout ?? ""}`;
  const err = `${res.stderr ?? ""}`;
  if (res.status !== 0) {
    fail(
      `[${ws.label}] turn ${turn}: pi exited ${res.status}\n` +
        `stdout:\n${out.slice(-2000)}\nstderr:\n${err.slice(-2000)}`,
    );
  } else {
    console.log(out.trim().slice(-800));
  }
  return { out, err, status: res.status, transcript: `${out}\n${err}` };
}

// ============================================================================
// OpenViking HTTP
// ============================================================================

async function ovFetch(path, init, baseUrl = OV_URL) {
  try {
    const resp = await fetch(`${baseUrl}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${OV_KEY}`,
        "Content-Type": "application/json",
        ...(init?.headers ?? {}),
      },
    });
    const body = await resp.json().catch(() => ({}));
    return { ok: resp.ok && body?.status !== "error", status: resp.status, body };
  } catch (error) {
    return { ok: false, status: 0, body: {}, error: String(error?.message ?? error) };
  }
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** `viking://…/sessions/<sid>` — canonical root, legacy alias as the fallback. */
async function sessionRootUri(ovSessionId) {
  const res = await ovFetch(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}`);
  const uri = typeof res.body?.result?.uri === "string" ? res.body.result.uri.trim() : "";
  if (uri.startsWith("viking://")) return uri.replace(/\/+$/, "");
  return `viking://session/${ovSessionId}`;
}

/** Archive directories under `<root>/history`, newest first. */
async function listArchives(rootUri) {
  const res = await ovFetch(
    `/api/v1/fs/ls?uri=${encodeURIComponent(`${rootUri}/history`)}&sort_by=name&sort_order=desc`,
  );
  const rows = Array.isArray(res.body?.result) ? res.body.result : [];
  const archives = [];
  for (const row of rows) {
    const uri = String(row?.uri ?? "").replace(/\/+$/, "");
    const id = uri.split("/").pop() || String(row?.name ?? "");
    if (!/^archive_\d+$/.test(id)) continue;
    archives.push({ archiveId: id, uri: uri || `${rootUri}/history/${id}` });
  }
  archives.sort((a, b) => Number(b.archiveId.slice(8)) - Number(a.archiveId.slice(8)));
  return archives;
}

/** `GET /content/read?uri=<archive>/<file>` — raw text, plus the HTTP status. */
async function readArchiveFile(archiveUri, file) {
  const res = await ovFetch(
    `/api/v1/content/read?uri=${encodeURIComponent(`${archiveUri}/${file}`)}`,
  );
  return { status: res.status, ok: res.ok, text: typeof res.body?.result === "string" ? res.body.result : "" };
}

/** Poll `.overview.md` until phase 2 has written it (25-55s on the target server). */
async function waitForOverview(archiveUri, budgetMs = 150_000) {
  const startedAt = Date.now();
  let last = { status: 0, ok: false, text: "" };
  while (Date.now() - startedAt < budgetMs) {
    last = await readArchiveFile(archiveUri, ".overview.md");
    if (last.status === 200 && last.text.trim()) break;
    await sleep(5000);
  }
  return { ...last, waitedMs: Date.now() - startedAt };
}

// ============================================================================
// Provider payload helpers
// ============================================================================

function payloadsFor(ws, turn) {
  return readdirSync(ws.outDir)
    .filter((f) => f.startsWith(`payload-t${turn}-`) && f.endsWith(".json"))
    .sort()
    .map((f) => {
      const raw = readFileSync(join(ws.outDir, f), "utf8");
      let payload = null;
      try {
        payload = JSON.parse(raw);
      } catch {
        payload = null;
      }
      return { file: f, raw, payload };
    });
}

const asArray = (value) => (Array.isArray(value) ? value : []);
const contentBlocks = (msg) => (Array.isArray(msg?.content) ? msg.content : []);

/** Text of a message under either provider shape. */
function messageText(msg) {
  if (typeof msg?.content === "string") return msg.content;
  return contentBlocks(msg)
    .filter((b) => b && (b.type === "text" || b.type === "input_text") && typeof b.text === "string")
    .map((b) => b.text)
    .join("\n");
}

function messagesOf(entry) {
  return asArray(entry?.payload?.messages);
}

/** The first message the model actually reads (system/developer are framing). */
function firstNonSystem(messages) {
  return messages.find((m) => m?.role !== "system" && m?.role !== "developer") ?? null;
}

/** `{id, name}` for every tool call in a message, both provider shapes. */
function toolCallsOf(msg) {
  const calls = [];
  for (const call of asArray(msg?.tool_calls)) {
    calls.push({ id: String(call?.id ?? ""), name: String(call?.function?.name ?? call?.name ?? "") });
  }
  for (const block of contentBlocks(msg)) {
    if (block?.type === "tool_use") calls.push({ id: String(block?.id ?? ""), name: String(block?.name ?? "") });
  }
  return calls;
}

/** `{id}` for every tool result in a message, both provider shapes. */
function toolResultsOf(msg) {
  const results = [];
  // openai-completions: a dedicated {role:"tool", tool_call_id} message.
  if (msg?.role === "tool") results.push({ id: String(msg?.tool_call_id ?? "") });
  // anthropic-messages: tool_result blocks carried on a user message.
  for (const block of contentBlocks(msg)) {
    if (block?.type === "tool_result") results.push({ id: String(block?.tool_use_id ?? "") });
  }
  return results;
}

/**
 * Orphan detection for both wire shapes.
 *
 * A tool result whose call was cut away — or a call whose result was cut away —
 * is rejected outright by the provider, and it is exactly what a sloppy cut
 * produces, so it is the single most important structural assertion here.
 */
function assertNoOrphanToolResults(messages, label) {
  const issued = new Set();
  const answered = new Set();
  const orphanResults = [];
  for (const msg of messages) {
    for (const call of toolCallsOf(msg)) {
      if (call.id) issued.add(call.id);
    }
    for (const result of toolResultsOf(msg)) {
      if (!result.id || !issued.has(result.id)) {
        orphanResults.push(result.id || "(missing id)");
      } else {
        answered.add(result.id);
      }
    }
  }
  const danglingCalls = [...issued].filter((id) => !answered.has(id));

  check(
    orphanResults.length === 0,
    `${label}: no orphan tool results${orphanResults.length ? ` (orphans: ${orphanResults.join(", ")})` : ""}`,
  );
  check(
    danglingCalls.length === 0,
    `${label}: no tool calls left without a result${danglingCalls.length ? ` (dangling: ${danglingCalls.join(", ")})` : ""}`,
  );
  return { orphanResults, danglingCalls };
}

/** Does any non-system message call `new_context` (or answer such a call)? */
function mentionsResetToolCall(messages) {
  const resetIds = new Set();
  let found = false;
  for (const msg of messages) {
    for (const call of toolCallsOf(msg)) {
      if (call.name === RESET_TOOL) {
        found = true;
        if (call.id) resetIds.add(call.id);
      }
    }
    for (const result of toolResultsOf(msg)) {
      if (result.id && resetIds.has(result.id)) found = true;
    }
  }
  return found;
}

function lastAssistant(messages) {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i]?.role === "assistant") return messages[i];
  }
  return null;
}

// ============================================================================
// Session file helpers
// ============================================================================

function readSessionEntries(ws) {
  const files = readdirSync(ws.sessionDir)
    .filter((f) => f.endsWith(".jsonl"))
    .sort();
  if (files.length === 0) return { file: "", entries: [] };
  const file = join(ws.sessionDir, files[files.length - 1]);
  const entries = [];
  for (const line of readFileSync(file, "utf8").split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      entries.push(JSON.parse(trimmed));
    } catch {
      // A partially flushed tail must not lose the entries before it.
    }
  }
  return { file, entries };
}

// ============================================================================
// Prompts
// ============================================================================

const T1_PROMPT =
  `Read the file ${RELEASE_FILE} in the working directory with the read tool, then tell me who owns ` +
  `the release. Also remember for the rest of this session: the release codename is ${CODENAME}, ` +
  `and the deploy window is ${DEPLOY_WINDOW}. Keep the answer to one short sentence. ${PAD1}`;

const T2_PROMPT =
  "Phase one is complete. Do exactly this, nothing else. " +
  `Make a single tool call to ${RESET_TOOL}, alone in its own turn, with no other tool call beside it. ` +
  `Use reason exactly: "${RESET_REASON}". ` +
  `In notes, state the release codename ${CODENAME}, the deploy window ${DEPLOY_WINDOW}, ` +
  `and that the next step is to write the phase two ${NEXT_STEP_MARKER}. ` +
  "After the new context window opens, reply with exactly: READY";

const T3_PROMPT = "What is the release codename I gave you earlier? Answer with just the codename.";

/**
 * The long scenario never mentions the context tools: the point is that the
 * agent reaches for them on its own once the window fills up. The work is
 * deliberately bigger than one window, and the running file means progress is
 * visible even across a reset.
 */
const LONG_T1_PROMPT =
  "Read TASK.md, then work through every file it lists, in order. For each file: read it with the " +
  "read tool, and append one entry to INVENTORY.md with the file name, what it is responsible for, " +
  "its main exports, and anything that looks risky. Tick the file off in TASK.md as you go. " +
  "Append after each file rather than holding everything until the end, and keep going until every " +
  "file is covered. Do not summarise the files you have not read yet.";

const LONG_T2_PROMPT =
  "Continue the inventory exactly where you left off: read TASK.md and INVENTORY.md first to see " +
  "what is already done, then carry on until every file is ticked off.";

const LONG_T3_PROMPT =
  "In the very first file you inventoried, what did you write down as its main responsibility, and " +
  "what was the first thing that file imports? Answer in two short lines.";

// ============================================================================
// Scenario: the main gate
// ============================================================================

async function runMainScenario() {
  const ws = makeWorkspace("main");
  const t1 = runTurn(ws, 1, T1_PROMPT);
  const t2 = runTurn(ws, 2, T2_PROMPT, { continueSession: true });
  const t3 = runTurn(ws, 3, T3_PROMPT, { continueSession: true });

  section("pi runs");
  check(
    t1.status === 0 && t2.status === 0 && t3.status === 0,
    `all three pi runs exited 0 (got ${t1.status}/${t2.status}/${t3.status})`,
  );

  // ---- T2 payloads: the cut ------------------------------------------------
  section("T2 provider payloads");
  const p2 = payloadsFor(ws, 2);
  check(p2.length >= 2, `T2 produced >=2 provider payloads (got ${p2.length})`);

  const postIndex = p2.findIndex((entry) => {
    const first = firstNonSystem(messagesOf(entry));
    return first?.role === "user" && messageText(first).startsWith(WINDOW_HEADER_OPEN);
  });
  check(postIndex >= 0, "T2 has a post-cut payload that starts with the window header");

  if (postIndex > 0) {
    const pre = p2[postIndex - 1];
    const post = p2[postIndex];
    const preMessages = messagesOf(pre);
    const postMessages = messagesOf(post);
    const header = messageText(firstNonSystem(postMessages));

    const preCall = lastAssistant(preMessages);
    if (preCall && toolCallsOf(preCall).some((c) => c.name === RESET_TOOL)) {
      pass(`pre-cut payload (${pre.file}) ends with an assistant ${RESET_TOOL} tool call`);
    } else {
      // The happy path is one sampling per turn: the model emits the
      // new_context call as its very first response, so the request that
      // elicited it cannot contain it yet. That is correct behaviour, not a
      // regression — but the pre-cut payload must still be the *old* window.
      warn(
        `pre-cut payload (${pre.file}) has no assistant ${RESET_TOOL} call: the model called it on ` +
          "its first sampling of the turn, so no captured request contains the call",
      );
      check(pre.raw.includes(PAD1), "pre-cut payload is the uncut window (still carries PAD1)");
      check(
        !messageText(firstNonSystem(preMessages)).startsWith(WINDOW_HEADER_OPEN),
        "pre-cut payload does not already start with a window header",
      );
    }

    check(header.includes('id="w2"'), 'window header is the second window (id="w2")');
    check(header.includes(CODENAME), `window header carries the codename ${CODENAME} from the notes`);
    check(!post.raw.includes(PAD1), "post-cut payload no longer contains the T1 padding");
    check(
      postMessages.length < preMessages.length,
      `post-cut payload has fewer messages (${postMessages.length} < ${preMessages.length})`,
    );
    assertNoOrphanToolResults(preMessages, "pre-cut payload");
    assertNoOrphanToolResults(postMessages, "post-cut payload");
    check(
      !mentionsResetToolCall(postMessages),
      `post-cut payload contains neither the ${RESET_TOOL} call nor its result`,
    );
  } else if (postIndex === 0) {
    fail("the very first T2 payload already carries the window header; no cut was observed in T2");
  }

  // ---- T3 payloads: the window survived the process boundary ---------------
  section("T3 provider payloads");
  const p3 = payloadsFor(ws, 3);
  check(p3.length > 0, `T3 produced a provider payload (got ${p3.length})`);
  if (p3.length > 0) {
    const last = p3[p3.length - 1];
    const first = firstNonSystem(messagesOf(last));
    check(
      first?.role === "user" && messageText(first).startsWith(WINDOW_HEADER_OPEN),
      "T3 payload still starts with the frozen window header (restored in a new process)",
    );
    check(!last.raw.includes(PAD1), "T3 payload still does not contain the T1 padding");
  }

  if (t3.out.includes(CODENAME)) pass(`T3 answer contains ${CODENAME}`);
  else warn(`T3 answer did not contain ${CODENAME}; inspect the window header and Working Memory`);

  // ---- Session file: window entry + status line ----------------------------
  section("pi session file");
  const { file: sessionFile, entries } = readSessionEntries(ws);
  check(Boolean(sessionFile), `session JSONL found (${sessionFile || "none"})`);
  const windowEntries = entries.filter(
    (e) => e?.type === "custom" && e?.customType === WINDOW_ENTRY_TYPE && e?.data,
  );
  const armedEntries = windowEntries.filter((e) => String(e.data?.archiveId ?? ""));
  const distinctWindows = new Set(armedEntries.map((e) => `${e.data.windowIndex}:${e.data.archiveId}`));
  check(
    distinctWindows.size === 1,
    `exactly one ${WINDOW_ENTRY_TYPE} window recorded (distinct windows: ${distinctWindows.size}, ` +
      `raw entries: ${windowEntries.length})`,
  );
  if (windowEntries.length > armedEntries.length + 1 || armedEntries.length > 1) {
    // Every persist() that sees a changed watermark writes another entry; only
    // a *second window* is a defect.
    warn(
      `${windowEntries.length} ${WINDOW_ENTRY_TYPE} entries in the session file ` +
        `(${armedEntries.length} armed): re-persists of the same window are expected`,
    );
  }
  const windowState = armedEntries.length > 0 ? armedEntries[armedEntries.length - 1].data : null;
  check(
    Boolean(windowState) && String(windowState.reason ?? "").includes(REASON_MARKER),
    `window entry records the reason (got "${String(windowState?.reason ?? "").slice(0, 80)}")`,
  );
  check(
    Boolean(windowState) && /^archive_\d+$/.test(String(windowState.archiveId ?? "")),
    `window entry records an archive id (got "${windowState?.archiveId ?? ""}")`,
  );
  const statusEntries = entries.filter(
    (e) =>
      (e?.type === "custom_message" || e?.type === "custom") && e?.customType === STATUS_CUSTOM_TYPE,
  );
  check(statusEntries.length >= 1, `session file has >=1 ${STATUS_CUSTOM_TYPE} message (got ${statusEntries.length})`);

  // ---- Debug log -----------------------------------------------------------
  section("debug log");
  const logPath = join(ws.outDir, "ov-pi.log");
  if (existsSync(logPath)) {
    const log = readFileSync(logPath, "utf8");
    check(log.includes("context-window: opened w2"), 'debug log records "context-window: opened w2"');
  } else {
    fail(`no debug log at ${logPath}`);
  }

  // ---- OpenViking ----------------------------------------------------------
  section("OpenViking archive");
  const sessionIdFile = join(ws.outDir, "session-id.txt");
  let ovSessionId = null;
  if (!existsSync(sessionIdFile)) {
    fail("probe did not record a pi session id");
  } else {
    ovSessionId = `pi-${readFileSync(sessionIdFile, "utf8").trim()}`;
    const rootUri = await sessionRootUri(ovSessionId);
    console.log(`e2e-window: OV session ${ovSessionId} root ${rootUri}`);

    const archives = await listArchives(rootUri);
    check(archives.length >= 1, `session has >=1 archive under <root>/history (got ${archives.length})`);

    if (archives.length > 0) {
      const archive = archives[0];
      if (windowState?.archiveId) {
        check(
          archive.archiveId === windowState.archiveId,
          `newest archive matches the window entry (${archive.archiveId} vs ${windowState.archiveId})`,
        );
      }

      const overview = await waitForOverview(archive.uri);
      check(
        overview.status === 200 && overview.text.trim().length > 0,
        `GET content/read ${archive.archiveId}/.overview.md is 200 with a body ` +
          `(status ${overview.status}, waited ${overview.waitedMs}ms)`,
      );

      const messages = await readArchiveFile(archive.uri, "messages.jsonl");
      check(messages.status === 200 && messages.text.length > 0, `${archive.archiveId}/messages.jsonl is readable`);
      check(messages.text.includes(HANDOFF_MARKER), `messages.jsonl contains "${HANDOFF_MARKER}"`);
      check(messages.text.includes(REASON_MARKER), `messages.jsonl contains the reason "${REASON_MARKER}"`);
      check(messages.text.includes("[tool-result"), 'messages.jsonl contains a "[tool-result" entry (tool output reached the archive)');

      const grep = await ovFetch("/api/v1/search/grep", {
        method: "POST",
        body: JSON.stringify({
          uri: `${rootUri}/history`,
          pattern: "ZEPHYR",
          case_insensitive: true,
        }),
      });
      const matches = asArray(grep.body?.result?.matches);
      check(matches.length >= 1, `grep "ZEPHYR" over <root>/history has >=1 match (got ${matches.length})`);
    }

    if (KEEP_OV_SESSION) {
      console.log(
        `e2e-window: keeping OV session ${ovSessionId} (E2E_KEEP_OV_SESSION=1); ` +
          `archives stay under <session root>/history — delete it yourself when done`,
      );
    } else {
      const del = await ovFetch(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}`, { method: "DELETE" });
      console.log(
        `e2e-window: cleanup OV session ${ovSessionId}: ${del.ok ? "deleted" : "FAILED; delete it manually"}`,
      );
    }
  }

  return ws;
}

// ============================================================================
// Scenario: fail-closed
// ============================================================================

/**
 * Highest context share the extension's own status line reported, and the
 * biggest provider request seen. Both are evidence that the window really did
 * fill up before the agent reset it.
 */
function contextPressureSeen(ws, entries) {
  let peakPercent = 0;
  let peakTokens = "";
  for (const entry of entries) {
    if (entry?.customType !== STATUS_CUSTOM_TYPE) continue;
    const text = typeof entry.content === "string" ? entry.content : "";
    const match = text.match(/~([\d.]+k?)\/(\S+)\s+tokens\s+\((\d+)%\)/);
    if (!match) continue;
    const percent = Number(match[3]);
    if (percent > peakPercent) {
      peakPercent = percent;
      peakTokens = `${match[1]}/${match[2]}`;
    }
  }
  let peakBytes = 0;
  let peakPayload = "";
  for (const turn of [1, 2, 3]) {
    for (const entry of payloadsFor(ws, turn)) {
      const bytes = messagesOf(entry).reduce((sum, m) => sum + JSON.stringify(m).length, 0);
      if (bytes > peakBytes) {
        peakBytes = bytes;
        peakPayload = entry.file;
      }
    }
  }
  // chars/4 is the same estimate the extension itself uses for non-CJK text.
  const peakEstimatedTokens = Math.round(peakBytes / 4);
  const peakEstimatedPercent = Math.round((peakEstimatedTokens / LONG_CONTEXT_WINDOW) * 100);
  return {
    peakPercent,
    peakTokens,
    peakBytes,
    peakPayload,
    peakEstimatedTokens,
    peakEstimatedPercent,
  };
}

/**
 * The OpenViking side of a scenario that does not control when the reset
 * happens: assert the archive exists and carries the handoff, without assuming
 * a particular reason string.
 */
async function checkOpenVikingSide(ws, { requireHandoff }) {
  section("long: OpenViking archive");
  const sessionIdFile = join(ws.outDir, "session-id.txt");
  if (!existsSync(sessionIdFile)) {
    fail("probe did not record a pi session id");
    return;
  }
  const ovSessionId = `pi-${readFileSync(sessionIdFile, "utf8").trim()}`;
  const rootUri = await sessionRootUri(ovSessionId);
  console.log(`e2e-window: OV session ${ovSessionId} root ${rootUri}`);

  const archives = await listArchives(rootUri);
  if (!requireHandoff && archives.length === 0) {
    warn("no archive: the agent never reset, so nothing was committed");
  } else {
    check(archives.length >= 1, `session has >=1 archive under <root>/history (got ${archives.length})`);
  }

  if (archives.length > 0) {
    const newest = archives[0];
    const overview = await waitForOverview(newest.uri);
    check(
      overview.status === 200 && overview.text.trim().length > 0,
      `${newest.archiveId}/.overview.md is readable, Working Memory generated ` +
        `(status ${overview.status}, waited ${overview.waitedMs}ms)`,
    );
    const messages = await readArchiveFile(newest.uri, "messages.jsonl");
    check(
      messages.status === 200 && messages.text.length > 0,
      `${newest.archiveId}/messages.jsonl is readable`,
    );
    if (messages.text) {
      check(messages.text.includes(HANDOFF_MARKER), `messages.jsonl contains "${HANDOFF_MARKER}"`);
      check(
        messages.text.includes("[tool-result"),
        'messages.jsonl contains a "[tool-result" entry (tool output reached the archive)',
      );
    }
  }

  if (KEEP_OV_SESSION) {
    console.log(
      `e2e-window: keeping OV session ${ovSessionId} (E2E_KEEP_OV_SESSION=1); ` +
        "delete it yourself when done",
    );
  } else {
    const del = await ovFetch(`/api/v1/sessions/${encodeURIComponent(ovSessionId)}`, { method: "DELETE" });
    console.log(
      `e2e-window: cleanup OV session ${ovSessionId}: ${del.ok ? "deleted" : "FAILED; delete it manually"}`,
    );
  }
}

// ============================================================================
// Scenario: long context, the agent resets under pressure on its own
// ============================================================================

async function runLongScenario() {
  const ws = makeWorkspace("long", {
    seedSourceTree: true,
    // Soft reminder well before pi's own compaction line, so the agent has room
    // to finish the file it is on, write notes and reset deliberately.
    windowConfig: { softPercent: LONG_SOFT_PERCENT, hardPercent: 70 },
  });
  // Reading 400 KB of source with reasoning on is slow: a turn that gets killed
  // mid-inventory looks like a product failure in the transcript when it is
  // only the harness giving up.
  const turnTimeoutMs = Number(process.env.E2E_WINDOW_LONG_TURN_TIMEOUT_MS ?? 1_500_000);
  const t1 = runTurn(ws, 1, LONG_T1_PROMPT, { timeoutMs: turnTimeoutMs });
  const t2 = runTurn(ws, 2, LONG_T2_PROMPT, { continueSession: true, timeoutMs: turnTimeoutMs });
  const t3 = runTurn(ws, 3, LONG_T3_PROMPT, { continueSession: true, timeoutMs: turnTimeoutMs });

  section("long: pi runs");
  check(
    t1.status === 0 && t2.status === 0 && t3.status === 0,
    `all three pi runs exited 0 (got ${t1.status}/${t2.status}/${t3.status})`,
  );

  const { file: sessionFile, entries } = readSessionEntries(ws);
  check(Boolean(sessionFile), "session JSONL found");

  section("long: context pressure");
  const pressure = contextPressureSeen(ws, entries);
  const { peakPercent, peakTokens, peakBytes, peakPayload, peakEstimatedTokens, peakEstimatedPercent } =
    pressure;
  console.log(
    `e2e-window: biggest provider request ${peakPayload} ${Math.round(peakBytes / 1024)} KB ` +
      `(~${Math.round(peakEstimatedTokens / 1000)}k tokens, ${peakEstimatedPercent}% of ` +
      `${Math.round(LONG_CONTEXT_WINDOW / 1000)}k)`,
  );
  console.log(
    `e2e-window: highest [context-status] line seen: ${peakPercent}% (${peakTokens || "none"}) — ` +
      "that line is emitted once per user prompt, so it undersamples a long tool-heavy turn",
  );
  // The payload is what actually went to the model; the status line is a sample.
  const reached = Math.max(peakPercent, peakEstimatedPercent);
  if (reached >= LONG_MIN_PERCENT) {
    pass(`context reached ${reached}% of the window (>= ${LONG_MIN_PERCENT}%)`);
  } else {
    warn(
      `context only reached ${reached}% (< ${LONG_MIN_PERCENT}%): the model stopped reading ` +
        "early, so this run does not exercise the pressure path",
    );
  }

  section("long: the agent's own decision");
  const resets = [];
  for (const entry of entries) {
    for (const msg of [entry?.message].filter(Boolean)) {
      for (const block of asArray(msg.content)) {
        if (block?.type === "toolCall" && block?.name === RESET_TOOL) {
          resets.push(block.arguments ?? {});
        }
      }
    }
  }
  const windowEntries = entries.filter((e) => e?.customType === WINDOW_ENTRY_TYPE);
  const armed = windowEntries.filter((e) => e?.data?.anchorToolCallId);
  if (resets.length === 0) {
    warn("the agent never called new_context: nothing was instructed, so this is model judgement");
  } else {
    pass(`the agent called ${RESET_TOOL} ${resets.length}x without being told to`);
    console.log(`e2e-window: reason: ${JSON.stringify(resets[0]?.reason ?? "")}`);
    console.log(`e2e-window: notes:  ${JSON.stringify(String(resets[0]?.notes ?? "").slice(0, 300))}`);
    check(
      String(resets[0]?.notes ?? "").length > 40,
      "the reset carried handoff notes of its own",
    );
    check(armed.length > 0, `a window was armed and persisted (${armed.length} entries)`);
  }

  section("long: the cut");
  if (resets.length === 0) {
    warn("no reset, so there is no cut to check");
  } else {
    const afterCut = [];
    for (const turn of [1, 2, 3]) {
      for (const entry of payloadsFor(ws, turn)) {
        const first = firstNonSystem(messagesOf(entry));
        if (first && messageText(first).startsWith(WINDOW_HEADER_OPEN)) afterCut.push(entry);
      }
    }
    check(afterCut.length > 0, `at least one provider request starts with the window header (${afterCut.length})`);
    for (const entry of afterCut.slice(0, 2)) {
      assertNoOrphanToolResults(messagesOf(entry), `post-cut payload ${entry.file}`);
    }
    const post = afterCut[0];
    if (post) {
      const postBytes = messagesOf(post).reduce((sum, m) => sum + JSON.stringify(m).length, 0);
      check(
        postBytes * 2 < peakBytes,
        `the reset more than halved the request (${Math.round(peakBytes / 1024)} KB -> ` +
          `${Math.round(postBytes / 1024)} KB)`,
      );
      check(
        !mentionsResetToolCall(messagesOf(post)),
        "the reset tool call and its result are gone from the new window",
      );
    }
  }

  section("long: work continued across the boundary");
  const inventory = join(ws.projDir, "INVENTORY.md");
  const taskFile = join(ws.projDir, "TASK.md");
  if (existsSync(inventory)) {
    const body = readFileSync(inventory, "utf8");
    const sourceFiles = readdirSync(join(ws.projDir, "src")).sort();
    const covered = sourceFiles.filter((f) => body.includes(f));
    console.log(`e2e-window: INVENTORY.md covers ${covered.length}/${sourceFiles.length} files`);
    check(covered.length > 0, "the agent produced an inventory");
    if (resets.length > 0 && covered.length <= 1) {
      warn("only one file made it into the inventory: the run stopped too early to show continuity");
    }
    if (existsSync(taskFile)) {
      const ticked = (readFileSync(taskFile, "utf8").match(/- \[x\]/gi) ?? []).length;
      console.log(`e2e-window: TASK.md ticked off ${ticked} files`);
    }
  } else {
    warn("no INVENTORY.md: the agent never started the task");
  }

  await checkOpenVikingSide(ws, { requireHandoff: resets.length > 0 });
  return ws;
}

async function runFailClosedScenario() {
  const ws = makeWorkspace("failclosed");
  console.log(`e2e-window: [failclosed] OPENVIKING_URL=${DEAD_OV_URL}`);
  const f1 = runTurn(ws, 1, T1_PROMPT, { ovUrl: DEAD_OV_URL });
  const f2 = runTurn(ws, 2, T2_PROMPT, { continueSession: true, ovUrl: DEAD_OV_URL });

  section("fail-closed");
  check(f1.status === 0 && f2.status === 0, `both fail-closed pi runs exited 0 (got ${f1.status}/${f2.status})`);

  const payloads = payloadsFor(ws, 2);
  check(payloads.length >= 1, `fail-closed T2 produced a provider payload (got ${payloads.length})`);
  if (payloads.length > 0) {
    const last = payloads[payloads.length - 1];
    check(last.raw.includes(PAD1), "fail-closed: the last payload still contains PAD1 (nothing was cut)");
    const first = firstNonSystem(messagesOf(last));
    check(
      !messageText(first).startsWith(WINDOW_HEADER_OPEN),
      "fail-closed: no window header was injected",
    );
    assertNoOrphanToolResults(messagesOf(last), "fail-closed last payload");
  }

  const logPath = join(ws.outDir, "ov-pi.log");
  const haystack = [
    f2.transcript,
    ...payloads.map((p) => p.raw),
    existsSync(logPath) ? readFileSync(logPath, "utf8") : "",
  ].join("\n");
  check(haystack.includes("NOT reset"), 'fail-closed: the refusal text "NOT reset" reached the model');

  return ws;
}

// ============================================================================
// Main
// ============================================================================

const workspaces = [];
try {
  if (RUN_MAIN) workspaces.push(await runMainScenario());
  if (RUN_LONG) workspaces.push(await runLongScenario());
  if (RUN_FAILCLOSED) workspaces.push(await runFailClosedScenario());
} catch (error) {
  fail(`unexpected error: ${error?.stack || error}`);
}

console.log("");
for (const ws of workspaces) {
  if (failures === 0 && !process.env.E2E_KEEP_TMP) {
    rmSync(ws.root, { recursive: true, force: true });
  } else {
    console.log(`e2e-window: [${ws.label}] workspace kept for inspection: ${ws.root}`);
  }
}

console.log(
  failures === 0
    ? `\ne2e-window: ALL CHECKS PASSED${warnings ? ` (${warnings} warning(s))` : ""}`
    : `\ne2e-window: ${failures} CHECK(S) FAILED${warnings ? `, ${warnings} warning(s)` : ""}`,
);
process.exit(failures === 0 ? 0 : 1);
