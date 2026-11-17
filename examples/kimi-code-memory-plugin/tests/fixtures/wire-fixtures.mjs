/**
 * Wire fixtures in the shape Kimi Code 2.6.2 actually writes.
 *
 * These are built, not pasted, so a test can say what a record means and then
 * vary one field of it. The shapes follow the host's `wire/record.ts`,
 * `wire/tree/tree.ts`, `wire/replayBuilder/fold.ts` and
 * `agent/contextMemory/loopEventFold.ts`.
 */

export const METADATA = {
  type: "metadata",
  protocol_version: "1.5",
  min_protocol_version: "1.5",
  created_at: 1760000000000,
};

export function metadata(overrides = {}) {
  return { ...METADATA, ...overrides };
}

let clock = 1760000000000;
function nextTime(step = 1000) {
  clock += step;
  return clock;
}

export function resetClock(start = 1760000000000) {
  clock = start;
}

export function turnPrompt({
  turnId = 0,
  promptId = `p${turnId}`,
  text = "hello",
  origin = { kind: "user" },
  trigger,
  time = nextTime(),
} = {}) {
  return {
    type: "turn.prompt",
    agentId: "main",
    turnId,
    promptId,
    input: [{ type: "text", text }],
    origin,
    ...(trigger === undefined ? {} : { trigger }),
    time,
  };
}

export function turnSteer({
  turnId = 0,
  messageId,
  promptIds,
  text = "steer text",
  origin = { kind: "user" },
  time = nextTime(),
} = {}) {
  return {
    type: "turn.steer",
    agentId: "main",
    turnId,
    ...(messageId === undefined ? {} : { messageId }),
    ...(promptIds === undefined ? {} : { promptIds }),
    input: [{ type: "text", text }],
    origin,
    time,
  };
}

export function turnEnded({ turnId = 0, reason = "completed", time = nextTime() } = {}) {
  return { type: "turn.ended", agentId: "main", turnId, reason, time };
}

export function turnCancel({ turnId = 0, target, time = nextTime() } = {}) {
  return { type: "turn.cancel", agentId: "main", turnId, ...(target === undefined ? {} : { target }), time };
}

export function stepBegin({ turnId = 0, step = 1, uuid, time = nextTime() } = {}) {
  return loopEvent({ type: "step.begin", turnId: String(turnId), step, uuid: uuid ?? `u-${turnId}-${step}`, time });
}

export function stepEnd({ turnId = 0, step = 1, uuid, time = nextTime() } = {}) {
  return loopEvent({ type: "step.end", turnId: String(turnId), step, uuid: uuid ?? `u-${turnId}-${step}`, time });
}

export function contentPart({ turnId = 0, step = 1, uuid, part, time = nextTime() } = {}) {
  return loopEvent({
    type: "content.part",
    turnId: String(turnId),
    step,
    stepUuid: uuid ?? `u-${turnId}-${step}`,
    part,
    time,
  });
}

export function toolCall({
  turnId = 0,
  step = 1,
  uuid,
  toolCallId = `call-${turnId}-${step}`,
  name = "Read",
  args = { file_path: "/tmp/a.txt" },
  time = nextTime(),
} = {}) {
  return loopEvent({
    type: "tool.call",
    turnId: String(turnId),
    step,
    stepUuid: uuid ?? `u-${turnId}-${step}`,
    toolCallId,
    name,
    args,
    time,
  });
}

export function toolResult({
  turnId = 0,
  toolCallId = "call-0-1",
  output = "file contents",
  isError = false,
  durationMs,
  note,
  time = nextTime(),
} = {}) {
  return loopEvent({
    type: "tool.result",
    turnId: String(turnId),
    toolCallId,
    result: {
      output,
      isError,
      ...(durationMs === undefined ? {} : { durationMs }),
      ...(note === undefined ? {} : { note }),
    },
    time,
  });
}

function loopEvent(event) {
  return { type: "context.append_loop_event", agentId: "main", event };
}

export function switched({ branch, baseBranch = "main", baseLine, legacyUndoLine, reason = "undo", time = nextTime() } = {}) {
  return {
    type: "agent.switched",
    agentId: "main",
    branch,
    base: { branch: baseBranch, line: baseLine },
    reason,
    ...(legacyUndoLine === undefined ? {} : { legacyUndoLine }),
    time,
  };
}

export function contextUndo({ count = 1, time = nextTime() } = {}) {
  return { type: "context.undo", agentId: "main", count, time };
}

export function contextUndone({ turns = 1, fromTurnId, time = nextTime() } = {}) {
  return {
    type: "context.undone",
    agentId: "main",
    turns,
    ...(fromTurnId === undefined ? {} : { fromTurnId }),
    time,
  };
}

export function contextClear({ time = nextTime() } = {}) {
  return { type: "context.clear", agentId: "main", time };
}

export function contextApplyCompaction({ summary = "condensed", time = nextTime() } = {}) {
  return { type: "context.apply_compaction", agentId: "main", summary, time };
}

export function appendMessage({ role = "user", text = "injected", origin, id, time = nextTime() } = {}) {
  return {
    type: "context.append_message",
    agentId: "main",
    message: {
      role,
      content: [{ type: "text", text }],
      ...(origin === undefined ? {} : { origin }),
      ...(id === undefined ? {} : { id }),
    },
    time,
  };
}

export function forked({ agentId = "main", time = nextTime() } = {}) {
  return { type: "forked", agentId, time };
}

/** One plain user turn with an assistant reply. */
export function simpleTurn(turnId = 0, { prompt = `question ${turnId}`, reply = `answer ${turnId}` } = {}) {
  return [
    turnPrompt({ turnId, text: prompt }),
    stepBegin({ turnId, step: 1 }),
    contentPart({ turnId, step: 1, part: { type: "text", text: reply } }),
    stepEnd({ turnId, step: 1 }),
    turnEnded({ turnId, reason: "completed" }),
  ];
}

/** Serialize records as the host does: one JSON object per line. */
export function wireText(records, { trailingNewline = true } = {}) {
  const body = records.map((record) => JSON.stringify(record)).join("\n");
  return trailingNewline ? `${body}\n` : body;
}

/** A metadata line followed by `records`. */
export function wireWithMetadata(records, meta = {}) {
  return wireText([metadata(meta), ...records]);
}
