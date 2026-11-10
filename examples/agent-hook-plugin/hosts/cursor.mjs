import { appendFile, open, readFile } from "node:fs/promises";

import {
  commitAgentSession,
  stableHash,
} from "../../memory-plugin-shared/lib/agent-hook-runtime.mjs";
import {
  extractPartsFromPayload,
  filterCaptureTurns,
  isCaptureEnabled,
  shapeCaptureParts,
} from "../../memory-plugin-shared/lib/capture-utils.mjs";
import { sendSessionMessages } from "../../memory-plugin-shared/lib/batch-send.mjs";
import { denyCursorPermission, evaluateUriGuard } from "../../memory-plugin-shared/lib/uri-guard.mjs";
import { parseCursorTranscript } from "./cursor-transcript.mjs";

async function readTranscript(ctx) {
  const transcriptPath = ctx.input.transcript_path || ctx.input.transcriptPath;
  if (!transcriptPath) return null;
  try { return parseCursorTranscript(await readFile(transcriptPath, "utf8")); } catch { return null; }
}

async function recordMcpResult(ctx) {
  const input = ctx.input;
  // The managed installation registers this exact server key. A tool named
  // "read" on another MCP server must never become an OpenViking read.
  if (input.mcp_server_name !== "openviking") return;
  if (typeof input.tool_use_id !== "string" || !input.tool_use_id.trim()
    || typeof input.tool_name !== "string" || !input.tool_name.trim()) {
    ctx.log("capture_skip", { reason: "missing_mcp_tool_identity" });
    return;
  }
  let toolInput, result;
  try {
    toolInput = JSON.parse(input.tool_input);
    result = JSON.parse(input.result_json);
  } catch {
    ctx.log("capture_skip", { reason: "invalid_mcp_tool_json" });
    return;
  }
  if (!toolInput || typeof toolInput !== "object" || Array.isArray(toolInput)
    || !result || typeof result !== "object" || Array.isArray(result)) {
    ctx.log("capture_skip", { reason: "invalid_mcp_tool_payload" });
    return;
  }
  const failed = Boolean(result.isError || result.error || result.rejected || result.permissionDenied);
  if (!failed && !Array.isArray(result.content)) {
    ctx.log("capture_skip", { reason: "missing_mcp_tool_result" });
    return;
  }
  const [part] = shapeCaptureParts(extractPartsFromPayload({
    type: "tool_result",
    tool_use_id: input.tool_use_id,
    name: `mcp__openviking__${input.tool_name}`,
    state: { input: toolInput, output: result },
    is_error: failed,
  }, { toolMaxChars: ctx.cfg.captureToolMaxChars }), "assistant", ctx.cfg).parts;
  // The hook's session lock also serializes this append. Keep result bodies
  // out of the checkpoint: each completion writes only its own record.
  const record = { afterTurn: (await readTranscript(ctx))?.length ?? null, part };
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
    // Hash the raw turn, not the filtered text, so raising captureMaxLength
    // never resends a turn the server already holds in truncated form.
    const hash = stableHash(index, turn.role, turn.content);
    if (capturedHashes.has(hash)) continue;
    const { kept, dropped } = filterCaptureTurns([turn], ctx.cfg);
    if (!kept.length) {
      ctx.log("capture_skip", dropped[0]);
      capturedHashes.add(hash);
      continue;
    }
    toSend.push({ hash, position: index * 2, turn: kept[0] });
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
  const acknowledged = toSend.slice(0, captured);
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
    complete: captured === toSend.length,
    state: {
      ...state,
      capturedHashes: [...capturedHashes].slice(-1000),
      capturedToolOffset,
      capturedToolIds: [...acceptedToolIds],
      capturedSinceCommit: Number(state.capturedSinceCommit || 0)
        + acknowledged.filter((item) => typeof item.turn.content === "string").length,
    },
  };
}

export const cursor = {
  prefix: "cu-",
  requestBudgets: {
    sessionStart: 25_000,
    beforeSubmitPrompt: 17_000,
    afterMCPExecution: 2_000,
    stop: 25_000,
    preCompact: 25_000,
    sessionEnd: 25_000,
  },
  stages: {
    sessionStart: "start",
    beforeSubmitPrompt: "prompt",
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
    return {};
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
    if (event === "afterMCPExecution") return recordMcpResult(ctx);
    const { state: next, complete } = await captureTranscript(ctx, state);
    if (!complete) return next;
    // Only a plain Stop waits for the threshold: a compaction or a session end
    // is the last chance this transcript has to reach the server.
    const shouldCommit = event !== "stop" || next.capturedSinceCommit >= ctx.cfg.commitTurnThreshold;
    if (shouldCommit) {
      const result = await commitAgentSession(ctx.fetchJSON, ctx.sessionId, ctx.log);
      if (result.ok || result.pendingQueued) next.capturedSinceCommit = 0;
    }
    return next;
  },
};
