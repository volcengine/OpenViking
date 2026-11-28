/**
 * Antigravity (the desktop app and the `agy` CLI share it).
 *
 * Antigravity calls PreInvocation before every model call, including each call
 * inside a tool loop (`invocationNum` > 0) and each reactive wakeup for a
 * background task, subagent message, or Stop continuation (`invocationNum` 0
 * with a higher `initialNumSteps`). Recall runs only when `invocationNum` is 0
 * and no `MODEL` step has run since the latest `USER_INPUT` step in
 * `transcriptPath`, keyed by that user step so a turn injects once. When the
 * new turn's user step is not in the transcript yet, `lastUserInput` differing
 * from the last recorded request marks it as a new turn. Injected context goes
 * back as a `systemMessage` step, which stays in the conversation. Stop carries
 * no message text; there is no session-end or compaction hook.
 *
 * PreToolUse hands over `toolCall.name` (`read_file`, `run_command`, …) and
 * `toolCall.args`, whose keys are PascalCase (`Path`, `CommandLine`).
 */

import { existsSync } from "node:fs";
import { basename, dirname, join } from "node:path";

import { sanitizeCapturedText } from "../../lib/capture-utils.mjs";
import { decode, latestCheckpoint, resolveTurnStep } from "./decode.mjs";

// File tools name their location in an argument ending in Path, File or
// Directory (`Path`, `AbsolutePath`, `TargetFile`, `DirectoryPath`). A file body
// or a command line that mentions a viking:// URI is not a location.
const LOCATION_ARG = /(?:Path|File|Directory)$/;
const GUARD_KINDS = [[/write/, "write"], [/replace|edit/, "edit"], [/grep|search/, "grep"], [/list|find/, "glob"]];

const INJECTS = new Set(["session-start", "pre-invocation"]);

function promptRead(payload, io) {
  if (Number(payload.invocationNum || 0) !== 0) return { skip: "tool-loop invocation" };
  const prompt = sanitizeCapturedText(payload.lastUserInput);
  if (!prompt) return { prompt: "" };
  const jsonl = payload.transcriptPath ? io.text(payload.transcriptPath) : null;
  const turnStep = jsonl === null ? undefined : resolveTurnStep(jsonl, payload.initialNumSteps, prompt);
  if (turnStep === null) return { skip: "wakeup" };
  return {
    prompt,
    // Keyed by the user step rather than initialNumSteps, which advances on wakeups.
    eventId: `${payload.conversationId}:${turnStep ?? payload.initialNumSteps}`,
    compaction: jsonl === null ? undefined : latestCheckpoint(jsonl),
  };
}

/** @type {import("../../lib/flow/host.d.mts").Host} */
export default {
  id: "antigravity",
  sessionPrefix: "ag-",
  events: { "session-start": "start", "pre-invocation": "prompt", stop: "turnEnd" },
  budgets: { "session-start": 25_000, "pre-invocation": 17_000, stop: 25_000 },
  canDetach: true,
  facts: { compactionDropsProfile: true },
  read(event, payload, io) {
    const sessionId = String(payload.conversationId || "");
    if (event === "pre-invocation") return { sessionId, ...promptRead(payload, io) };
    return { sessionId, transcript: payload.transcriptPath || "" };
  },
  // The host may hand over `transcript.jsonl`, whose step contents are cut
  // short; the untruncated `transcript_full.jsonl` beside it is read instead.
  locate(path) {
    if (!path || basename(String(path)) !== "transcript.jsonl") return path ? String(path) : null;
    const full = join(dirname(String(path)), "transcript_full.jsonl");
    return existsSync(full) ? full : String(path);
  },
  decode,
  tool(payload) {
    const { name = "", args } = payload.toolCall || {};
    const location = Object.entries(args || {}).find(([key, value]) => (
      LOCATION_ARG.test(key) && typeof value === "string" && /^\s*viking:\/\//i.test(value)
    ))?.[1];
    if (!location) return null;
    const kind = GUARD_KINDS.find(([pattern]) => pattern.test(name))?.[1] || "read";
    return { name: kind, input: { path: location.trim() } };
  },
  emit(event, output) {
    // No decision at all when the call may proceed: "allow" would skip the
    // user's own permission prompt.
    if (output.deny) return { decision: "deny", reason: output.deny };
    if (!event) return null;
    return INJECTS.has(event) && output.context
      ? { injectSteps: [{ systemMessage: { systemMessage: output.context } }] }
      : {};
  },
};
