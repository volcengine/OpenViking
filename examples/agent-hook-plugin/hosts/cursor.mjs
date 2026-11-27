import { appendFile, open, readFile } from "node:fs/promises";

import {
  commitAgentSession,
  stableHash,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import { buildCaptureMessage, isCaptureEnabled } from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { sendSessionMessages } from "../../memory-plugin-shared/lib/batch-send.mjs";
import { commitsOnCompaction } from "../../memory-plugin-shared/lib/session-model.mjs";
import { denyCursorPermission, evaluateUriGuard } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import { parseCursorTranscript } from "./cursor-transcript.mjs";
import { withoutHistorySession } from "./history-session.mjs";

const TOOL_EVENTS = new Set(["postToolUse", "postToolUseFailure", "afterMCPExecution"]);

// Cursor prefixes an MCP server's key with where it was configured:
// `user-openviking`, `project-<path>-openviking`, `plugin-<plugin>-openviking`.
const OPENVIKING_MCP_SERVER = /^(?:(?:user|project-.+|plugin-.+)-)?openviking$/;

async function readTranscript(ctx) {
  const transcriptPath = ctx.input.transcript_path || ctx.input.transcriptPath;
  if (!transcriptPath) return null;
  try { return parseCursorTranscript(await readFile(transcriptPath, "utf8")); } catch { return null; }
}

function nonEmpty(value) {
  return typeof value === "string" && value.trim() !== "";
}

function parseMcpCompletion(ctx, input) {
  if (!nonEmpty(input.mcp_server_name)) {
    ctx.log("capture_skip", { reason: "missing_mcp_server" });
    return null;
  }
  let toolInput, result;
  try {
    toolInput = JSON.parse(input.tool_input);
    result = JSON.parse(input.result_json);
  } catch {
    ctx.log("capture_skip", { reason: "invalid_mcp_tool_json" });
    return null;
  }
  if (!toolInput || typeof toolInput !== "object" || Array.isArray(toolInput)
    || !result || typeof result !== "object" || Array.isArray(result)) {
    ctx.log("capture_skip", { reason: "invalid_mcp_tool_payload" });
    return null;
  }
  const failed = Boolean(result.isError || result.error || result.rejected || result.permissionDenied);
  if (!failed && !Array.isArray(result.content)) {
    ctx.log("capture_skip", { reason: "missing_mcp_tool_result" });
    return null;
  }
  // The server recognises its own tools by the `mcp__openviking__` name, so a
  // tool called "read" on another server must keep that server's name.
  const server = OPENVIKING_MCP_SERVER.test(input.mcp_server_name)
    ? "openviking"
    : input.mcp_server_name.replace(/^user-/, "");
  return { name: `mcp__${server}__${input.tool_name}`, input: toolInput, output: result, failed };
}

/**
 * One finished tool call as a sidecar record. Cursor's own tools report through
 * postToolUse / postToolUseFailure; MCP calls through afterMCPExecution, the only
 * event that names the server, so the post events skip their `MCP:<tool>` copy.
 */
async function recordToolCompletion(ctx, event) {
  const input = ctx.input;
  if (!nonEmpty(input.tool_use_id) || !nonEmpty(input.tool_name)) {
    ctx.log("capture_skip", { reason: "missing_tool_identity" });
    return;
  }
  let completion;
  if (event === "afterMCPExecution") {
    completion = parseMcpCompletion(ctx, input);
  } else if (input.tool_name.startsWith("MCP:")) {
    return;
  } else if (event === "postToolUseFailure") {
    completion = {
      name: input.tool_name,
      input: input.tool_input,
      output: input.error_message || input.failure_type || "",
      failed: true,
    };
  } else {
    completion = { name: input.tool_name, input: input.tool_input, output: input.tool_output ?? "", failed: false };
  }
  if (!completion) return;
  const message = buildCaptureMessage({
    type: "tool_result",
    tool_use_id: input.tool_use_id,
    name: completion.name,
    state: { input: completion.input, output: completion.output },
    is_error: completion.failed,
  }, "assistant", ctx.cfg);
  if (!message) return;
  // The hook's session lock also serializes this append. Keep result bodies
  // out of the checkpoint: each completion writes only its own record.
  const record = { afterTurn: (await readTranscript(ctx))?.length ?? null, part: message.parts[0] };
  await appendFile(`${ctx.stateFile}.tools.jsonl`, `${JSON.stringify(record)}\n`, { mode: 0o600 });
}

async function readToolRecords(ctx, offset) {
  let file;
  let tail;
  try {
    file = await open(`${ctx.stateFile}.tools.jsonl`, "r");
    const { size } = await file.stat();
    if (offset > size) throw new Error("Cursor tool log is shorter than its capture checkpoint");
    tail = Buffer.alloc(size - offset);
    for (let position = 0; position < tail.length;) {
      const { bytesRead } = await file.read(tail, position, tail.length - position, offset + position);
      if (!bytesRead) throw new Error("Cursor tool log ended before its recorded size");
      position += bytesRead;
    }
  } catch (error) {
    if (error.code === "ENOENT" && offset === 0) return [];
    throw error;
  } finally {
    await file?.close();
  }
  if (tail.length && tail.at(-1) !== 10) throw new Error("Cursor tool log has an incomplete record");
  return tail.toString("utf8").split("\n").slice(0, -1).map((line) => {
    offset += Buffer.byteLength(line, "utf8") + 1;
    return { ...JSON.parse(line), endOffset: offset };
  });
}

async function captureTranscript(ctx, state) {
  const turns = await readTranscript(ctx);
  if (!turns) {
    ctx.log("capture_skip", { reason: "unreadable_transcript" });
    return { state, complete: false };
  }
  let capturedToolOffset = state.capturedToolOffset || 0;
  const toolRecords = await readToolRecords(ctx, capturedToolOffset);
  // Keep acknowledgement identities in the checkpoint, so an old completion
  // can be deduplicated without rereading its result body.
  const acceptedToolIds = new Set(state.capturedToolIds || []);
  const seenToolIds = new Set(acceptedToolIds);
  const capturedHashes = new Set(Array.isArray(state.capturedHashes) ? state.capturedHashes : []);
  const toSend = [];
  for (const [index, turn] of turns.entries()) {
    // Cursor transcripts do not expose a stable message id. Include the
    // transcript position so two legitimate identical turns are retained,
    // while duplicate Hook executions over the same transcript still dedupe.
    const hash = stableHash(index, turn.role, turn.content);
    if (capturedHashes.has(hash)) continue;
    const message = buildCaptureMessage({ role: turn.role, content: turn.content }, turn.role, ctx.cfg);
    if (!message) {
      capturedHashes.add(hash);
      continue;
    }
    toSend.push({ hash, position: index * 2, turn: message });
  }
  let toolPosition = -1;
  for (const item of toolRecords) {
    if (seenToolIds.has(item.part.tool_id)) continue;
    seenToolIds.add(item.part.tool_id);
    // Preserve tool-log order even when compaction shortened the text source.
    toolPosition = Math.max(toolPosition, (item.afterTurn ?? turns.length) * 2 - 1);
    toSend.push({
      toolId: item.part.tool_id,
      position: toolPosition,
      turn: { role: "assistant", parts: [item.part] },
    });
  }
  toSend.sort((a, b) => a.position - b.position);
  // Both local sources own retries. Advance only after upload, without a
  // second copy in the offline queue.
  const payloads = toSend.map(({ turn }) => ({
    ...turn,
    ...(ctx.peerId ? { peer_id: ctx.peerId } : {}),
  }));
  const result = await sendSessionMessages(ctx.fetchJSON, ctx.sessionId, payloads);
  const captured = result.sent;
  // Refused messages are acknowledged too; only the unsent tail is kept.
  const settled = toSend.length - result.retryLater - result.enqueueFailed;
  const acknowledged = toSend.slice(0, settled);
  for (const item of acknowledged) {
    if (item.toolId) acceptedToolIds.add(item.toolId);
    else capturedHashes.add(item.hash);
  }
  for (const item of toolRecords) {
    if (!acceptedToolIds.has(item.part.tool_id)) break;
    capturedToolOffset = item.endOffset;
  }
  return {
    captured,
    complete: settled === toSend.length,
    state: {
      ...state,
      capturedHashes: [...capturedHashes].slice(-1000),
      capturedToolOffset,
      capturedToolIds: [...acceptedToolIds].slice(-1000),
      // Tool records do not count toward commitTurnThreshold.
      capturedSinceCommit: Number(state.capturedSinceCommit || 0)
        + acknowledged.filter((item) => !item.toolId).length,
    },
  };
}

export const cursor = {
  prefix: "cu-",
  requestBudgets: {
    sessionStart: 25_000,
    beforeSubmitPrompt: 17_000,
    preToolUse: 1_000,
    postToolUse: 2_000,
    postToolUseFailure: 2_000,
    afterMCPExecution: 2_000,
    stop: 25_000,
    preCompact: 25_000,
    sessionEnd: 25_000,
  },
  stages: {
    sessionStart: "start",
    beforeSubmitPrompt: "prompt",
    preToolUse: "fill",
    postToolUse: "capture",
    postToolUseFailure: "capture",
    afterMCPExecution: "capture",
    stop: "capture",
    preCompact: "capture",
    sessionEnd: "capture",
  },
  envelope(event, block) {
    if (event === "beforeSubmitPrompt") {
      return block ? { continue: true, additional_context: block } : { continue: true };
    }
    if (event === "sessionStart" && block) return { additional_context: block };
    if (event === "preToolUse" && block) return block;
    return {};
  },
  // preCompact is observe-only and nothing runs after a compaction, so the note
  // waits for the next beforeSubmitPrompt.
  historyPointer: {
    sessionDefault: true,
    defer: (event) => (event === "preCompact" ? "compact" : ""),
  },
  // preToolUse names a local MCP tool `MCP:<tool>` without its server, so the
  // exact tool name and a missing `session` are the whole guard.
  fillHistorySession(input, session) {
    if (input.tool_name !== "MCP:session_history") return null;
    const args = withoutHistorySession(input.tool_input);
    return args ? { updated_input: { ...args, session } } : null;
  },
  guard(input = {}) {
    // beforeShellExecution is no longer installed, but a hooks.json from an
    // older install may still route a shell command here, and it must run.
    if (typeof input.command === "string") return {};
    const decision = evaluateUriGuard("read", input);
    return decision ? denyCursorPermission(decision.reason) : {};
  },
  prompt: (input) => (typeof input.prompt === "string" ? input.prompt.trim() : ""),
  async capture(ctx, state, event) {
    if (!isCaptureEnabled(ctx.cfg)) return state;
    if (TOOL_EVENTS.has(event)) return recordToolCompletion(ctx, event);
    const { state: next, complete } = await captureTranscript(ctx, state);
    if (!complete) return next;
    // Only a plain Stop waits for the threshold: a compaction or a session end
    // is the last chance this transcript has to reach the server.
    const shouldCommit = event === "stop"
      ? next.capturedSinceCommit >= ctx.cfg.commitTurnThreshold
      : event !== "preCompact" || commitsOnCompaction(ctx.cfg);
    if (shouldCommit) {
      const result = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (result.ok || result.pendingQueued) next.capturedSinceCommit = 0;
    }
    return next;
  },
};
