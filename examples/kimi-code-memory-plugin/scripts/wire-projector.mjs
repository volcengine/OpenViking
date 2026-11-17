/**
 * Project one agent's wire records into the conversation that was actually
 * visible on its active branch.
 *
 * Two things make this harder than "read every record in order":
 *
 * 1. **The file is a tree.** `agent.switched` records move the agent onto a new
 *    branch whose base is `{branch, line}` in an older branch. What the agent
 *    saw is the recursive `activeChain`: the base branch up to `base.line`,
 *    then the new branch from the switch onward. A single last-`base.line`
 *    truncation is only the one-undo special case and gets multi-switch
 *    histories wrong. This mirrors `wire/tree/tree.ts`'s `parseTree` and
 *    `activeChain` from the host, including their failure modes: an unknown
 *    base branch or a cycle is an error, not something to paper over.
 * 2. **A turn is a state machine.** `turn.prompt` opens it, `turn.steer`
 *    closes the current logical segment and opens the next, loop events carry
 *    the assistant text and tool evidence, and only `turn.ended` closes it. A
 *    `turn.cancel` records intent and is not a terminal state; a turn with no
 *    `turn.ended` is open and must be re-read later, not invented.
 *
 * The projector is pure: records in, projection out. The capture stage decides
 * what to send and what to keep for the next event.
 */

const MAIN_BRANCH = "main";

/**
 * How large one tool call's arguments may get before they are replaced by a
 * truncated string. The object form is what the model wrote, and it is kept as
 * an object — but a pathological one (a 10 MB file body in a Write call) must
 * not be sent whole, so it is measured after serialization too.
 */
const MAX_TOOL_INPUT_CHARS = 200_000;

/**
 * The switch edge a record describes, or `undefined` when it is malformed.
 * A malformed switch is recorded as a diagnostic and skipped, exactly as the
 * host does, because one damaged line must not stop the whole projection.
 */
function readSwitchEdge(record, line) {
  if (record?.type !== "agent.switched") return undefined;
  const branch = record.branch;
  const base = record.base;
  if (typeof branch !== "string") return undefined;
  if (base === null || typeof base !== "object" || Array.isArray(base)) return undefined;
  const baseBranch = base.branch;
  const baseLine = base.line;
  if (typeof baseBranch !== "string" || typeof baseLine !== "number") return undefined;
  return {
    line,
    branch,
    base: { branch: baseBranch, line: baseLine },
    reason: typeof record.reason === "string" ? record.reason : undefined,
    legacyUndoLine: typeof record.legacyUndoLine === "number" ? record.legacyUndoLine : undefined,
  };
}

/**
 * Split the file into segments, one per branch, following the host's parse.
 *
 * `segments` maps a branch to the lines that belong to it. The first segment
 * starts on `main` at line 1; each switch closes the current segment at the
 * switch's line and opens the next at the following line.
 */
export function parseWireTree(entries, lastLine, { carriedEdges = [] } = {}) {
  // A scan window may begin after the switch that owns the branch it is reading.
  // The edges that came before it are still the branch geometry, so they are
  // carried in from the persisted state rather than re-derived from a window
  // that no longer contains them. Without this a switch recorded in an earlier
  // event makes every later projection fail with "unknown branch".
  const edges = carriedEdges.filter((edge) => Number.isInteger(edge?.line)
    && typeof edge?.branch === "string"
    && Number.isInteger(edge?.base?.line)
    && typeof edge?.base?.branch === "string");
  const malformedSwitchLines = [];
  const pairedLegacyUndoLines = new Set(edges
    .filter((edge) => edge.legacyUndoLine !== undefined)
    .map((edge) => edge.legacyUndoLine));

  for (const { record, line } of entries) {
    if (record?.type !== "agent.switched") continue;
    const edge = readSwitchEdge(record, line);
    if (edge === undefined) {
      malformedSwitchLines.push(line);
      continue;
    }
    if (edges.some((existing) => existing.line === edge.line)) continue;
    edges.push(edge);
    if (edge.legacyUndoLine !== undefined) pairedLegacyUndoLines.add(edge.legacyUndoLine);
  }
  edges.sort((a, b) => a.line - b.line);

  // Build every segment the carried edges describe, not just the ones this
  // window happens to contain. A window that starts after the switch still has
  // to know that `main` existed up to `base.line`, or `activeChain` cannot walk
  // back to it and the whole projection fails with "unknown branch".
  const segments = [];
  const duplicateBranches = [];
  const seenBranches = new Set([MAIN_BRANCH]);
  let branch = MAIN_BRANCH;
  let fromLine = 1;
  let opening;
  for (const edge of edges) {
    segments.push({ branch, edge: opening, fromLine, toLine: edge.line });
    if (seenBranches.has(edge.branch) && !duplicateBranches.includes(edge.branch)) {
      duplicateBranches.push(edge.branch);
    }
    seenBranches.add(edge.branch);
    branch = edge.branch;
    opening = edge;
    fromLine = edge.line + 1;
  }
  segments.push({
    branch,
    edge: opening,
    fromLine,
    toLine: Math.max(lastLine, fromLine - 1),
  });

  return {
    edges,
    segments,
    pairedLegacyUndoLines,
    activeBranch: branch,
    diagnostics: { malformedSwitchLines, duplicateBranches },
  };
}

/**
 * The records on the active branch, in file order.
 *
 * Walks from the active branch back through each segment's base, collecting
 * that base's records up to `base.line`, then the current segment from the
 * switch onward. A cycle or an unknown branch throws: the projection would be
 * arbitrary, and silently capturing the wrong turns is worse than stopping.
 */
export function activeChain(entries, tree) {
  const byBranch = new Map(tree.segments.map((segment) => [segment.branch, segment]));
  const out = [];
  const visited = new Set();

  const walk = (branch, upto) => {
    if (visited.has(branch)) {
      throw new Error(`agent.switched base chain cycles at branch '${branch}'`);
    }
    visited.add(branch);
    const segment = byBranch.get(branch);
    if (segment === undefined) {
      throw new Error(`agent.switched base chain references unknown branch '${branch}'`);
    }
    if (segment.edge !== undefined) walk(segment.edge.base.branch, segment.edge.base.line);
    const floor = segment.edge === undefined ? 1 : segment.edge.line + 1;
    const ceil = upto ?? segment.toLine;
    for (const entry of entries) {
      if (entry.record?.type === "agent.switched") continue;
      if (entry.line < floor || entry.line > ceil) continue;
      out.push(entry);
    }
  };

  walk(tree.activeBranch, undefined);
  return out;
}

/** The projection's view of one logical turn. */
function newTurn({ turnId, promptId, line, time }) {
  return {
    turnId,
    promptId,
    startLine: line,
    time,
    segments: [],
    open: true,
    ended: undefined,
    cancelRequested: false,
  };
}

function newSegment({ index, messageId, input, origin, trigger, time }) {
  return {
    index,
    messageId,
    input: input ?? [],
    origin,
    trigger,
    time,
    parts: [],
    steps: [],
    openStepUuid: null,
    openStepParts: null,
  };
}

function textFromInput(input) {
  if (typeof input === "string") return input;
  if (!Array.isArray(input)) return "";
  const chunks = [];
  for (const part of input) {
    if (typeof part === "string") chunks.push(part);
    else if (part && typeof part === "object" && part.type === "text" && typeof part.text === "string") {
      chunks.push(part.text);
    }
    // think, image_url, audio_url, video_url and tool_call parts carry no
    // user-visible text and are not mapped: the design captures text only.
  }
  return chunks.filter(Boolean).join("\n");
}

function stringifyToolInput(args) {
  if (args === undefined) return undefined;
  if (args !== null && typeof args === "object" && !Array.isArray(args)) {
    // The object is what the model actually wrote, so it stays an object. Only
    // a payload past the ceiling is replaced, and then by its serialized head:
    // sending a 10 MB Write body as structured JSON is the case this cap is for.
    let serialized;
    try {
      serialized = JSON.stringify(args);
    } catch {
      return undefined;
    }
    if (typeof serialized !== "string") return args;
    return serialized.length > MAX_TOOL_INPUT_CHARS
      ? { value: `${serialized.slice(0, MAX_TOOL_INPUT_CHARS)}\n[truncated]` }
      : args;
  }
  // The OpenViking message contract requires an object here; a scalar argument
  // is wrapped rather than dropped, so the evidence survives.
  const text = typeof args === "string" ? args : JSON.stringify(args);
  if (text === undefined) return undefined;
  return text.length > MAX_TOOL_INPUT_CHARS ? { value: `${text.slice(0, MAX_TOOL_INPUT_CHARS)}\n[truncated]` } : { value: text };
}

function textOfResultOutput(output) {
  if (typeof output === "string") return output;
  if (Array.isArray(output)) {
    return output
      .map((part) => (typeof part === "string" ? part : part?.type === "text" && typeof part.text === "string" ? part.text : ""))
      .filter(Boolean)
      .join("\n");
  }
  if (output && typeof output === "object" && typeof output.text === "string") return output.text;
  return "";
}

/**
 * Fold loop events into the turn's open segment.
 *
 * The host's own fold (`loopEventFold.ts`) is the model: `step.begin` opens an
 * assistant message, `content.part`/`tool.call` only count when they name that
 * step's uuid, `step.end` seals it, and a `tool.result` only closes a call that
 * is still pending. Text and tool parts keep their arrival order, which is what
 * the model saw.
 */
function applyLoopEvent(turn, segment, event, line, diagnostics) {
  if (!event || typeof event !== "object") return;
  const type = event.type;

  if (type === "step.begin") {
    if (segment.openStepParts) sealStep(segment);
    segment.openStepUuid = typeof event.uuid === "string" ? event.uuid : null;
    segment.openStepParts = { uuid: segment.openStepUuid, line, parts: [], toolCalls: [], results: [] };
    segment.steps.push(segment.openStepParts);
    return;
  }

  if (type === "step.end") {
    // `step.end` closes the step, never the turn.
    if (segment.openStepParts && (event.uuid === undefined || event.uuid === segment.openStepUuid)) sealStep(segment);
    return;
  }

  if (type === "content.part") {
    const step = acceptStep(segment, event.stepUuid);
    if (!step) return;
    const part = event.part;
    if (!part || typeof part !== "object") return;
    // Only text is conversation. `think` is the model's private reasoning and
    // is deliberately dropped.
    if (part.type === "text" && typeof part.text === "string" && part.text) {
      step.parts.push({ type: "text", text: part.text, line });
    }
    return;
  }

  if (type === "tool.call") {
    const step = acceptStep(segment, event.stepUuid);
    if (!step) return;
    const id = typeof event.toolCallId === "string" ? event.toolCallId : undefined;
    if (!id) {
      diagnostics.missingToolCallIds += 1;
      return;
    }
    const call = {
      type: "tool",
      tool_id: id,
      tool_name: typeof event.name === "string" ? event.name : undefined,
      tool_input: stringifyToolInput(event.args),
      tool_status: "running",
      line,
    };
    step.parts.push(call);
    step.toolCalls.push({ id, name: call.tool_name, part: call });
    return;
  }

  if (type === "tool.result") {
    const id = typeof event.toolCallId === "string" ? event.toolCallId : undefined;
    if (!id) {
      diagnostics.missingToolCallIds += 1;
      return;
    }
    const pending = findPendingCall(segment, id);
    if (!pending) {
      // A result with no call is not evidence of anything the model saw in
      // this branch; it is counted, never invented into a message.
      diagnostics.orphanToolResults += 1;
      return;
    }
    const result = event.result && typeof event.result === "object" ? event.result : {};
    pending.call.resolved = true;
    pending.call.result = {
      tool_id: id,
      tool_name: pending.call.name,
      tool_output: textOfResultOutput(result.output),
      isError: result.isError === true,
      durationMs: typeof result.durationMs === "number" ? result.durationMs : undefined,
      note: typeof result.note === "string" ? result.note : undefined,
      line,
    };
    // The step the result arrived in, so the projection can put it back in the
    // order the model saw: the call, then its result, then the next step.
    pending.step.results.push(pending.call.result);
    return;
  }

  diagnostics.otherLoopEvents += 1;
}

function acceptStep(segment, stepUuid) {
  const step = segment.openStepParts;
  if (!step) return null;
  if (stepUuid !== undefined && stepUuid !== null && step.uuid !== null && stepUuid !== step.uuid) return null;
  return step;
}

function sealStep(segment) {
  if (!segment.openStepParts) return;
  segment.openStepParts.sealed = true;
  segment.openStepParts = null;
  segment.openStepUuid = null;
}

function findPendingCall(segment, toolCallId) {
  for (const step of segment.steps) {
    for (const call of step.toolCalls) {
      if (call.id === toolCallId && !call.resolved) return { call, step };
    }
  }
  return null;
}

/**
 * Project a list of `{line, record}` entries.
 *
 * `initial` carries the projection state a previous read left behind, because
 * a turn can span two hook invocations: the prompt arrives during the user's
 * turn and `turn.ended` during the next event. The projector therefore works
 * on a list of *all* records it has seen (the reader's cursor makes that
 * incremental) and reports which turns are complete, terminal-partial, or
 * still open.
 *
 * Returned turns:
 *   { turnId, promptId, segments, complete, terminal, reason, startLine, endLine }
 *
 * `complete` is true only for `turn.ended` with `reason: "completed"`; a
 * `failed`/`cancelled` turn is `terminal` but partial. An open turn is neither,
 * and the caller must not send it as a finished turn.
 */
export function projectWire(entries, { lastLine, carriedEdges = [] } = {}) {
  const last = lastLine ?? (entries.length ? entries[entries.length - 1].line : 0);
  const tree = parseWireTree(entries, last, { carriedEdges });
  const diagnostics = {
    malformedSwitches: tree.diagnostics.malformedSwitchLines.length,
    duplicateBranches: tree.diagnostics.duplicateBranches,
    missingToolCallIds: 0,
    orphanToolResults: 0,
    otherLoopEvents: 0,
    cancelledTurns: 0,
    undoneTurns: 0,
    clearedTurns: 0,
    compactions: 0,
  };

  let chain;
  let chainError;
  try {
    chain = activeChain(entries, tree);
  } catch (error) {
    return {
      status: "error",
      error: error?.message || "active_chain_failed",
      turns: [],
      diagnostics,
      tree,
    };
  }

  const turns = [];
  // The host numbers logical turns with a clock and maps each wire `turnId` to
  // the logical turn currently occupying that source id. A steer does not extend
  // the current turn: it closes it as `completed` and opens a *new* logical turn
  // with a new id (`normalizeLegacyTurnSteerRecords` in `wire/human.ts`). So a
  // `turn.ended` naming the source id closes whichever segment is mapped to it,
  // which after a steer is the steered one.
  const segmentsBySource = new Map();
  const cancelledTurnIds = new Set();
  let active = null;
  let nextTurnId = 0;

  const advanceClock = (from) => {
    let value = from;
    while (cancelledTurnIds.delete(value)) value += 1;
    return value;
  };

  const startTurn = ({ sourceTurnId, explicitTurnId, promptId, line, time, input, origin, trigger }) => {
    const assigned = Math.max(nextTurnId, Number.isInteger(explicitTurnId) ? explicitTurnId : 0);
    nextTurnId = advanceClock(assigned + 1);
    const turn = newTurn({ turnId: assigned, promptId, line, time });
    turn.sourceTurnId = sourceTurnId;
    turn.segments.push(newSegment({ index: 0, messageId: promptId, input, origin, trigger, time }));
    turns.push(turn);
    segmentsBySource.set(sourceTurnId, turn);
    active = turn;
    return turn;
  };

  const closeTurn = (turn, reason, line) => {
    if (!turn || !turn.open) return;
    turn.open = false;
    turn.ended = { reason: typeof reason === "string" ? reason : "unknown", line };
    turn.endLine = line;
    // A step still open when the turn ends is a partial capture, whatever the
    // end reason says: its output may be incomplete.
    turn.hadOpenStep = turn.segments.some((segment) => segment.openStepParts !== null);
    for (const segment of turn.segments) sealStep(segment);
    if (active === turn) active = null;
  };

  for (const entry of chain) {
    const { record, line } = entry;
    if (!record) continue;
    const type = record.type;

    if (type === "turn.prompt") {
      const explicit = Number.isInteger(record.turnId) ? record.turnId : undefined;
      const sourceTurnId = explicit ?? nextTurnId;
      const promptId = typeof record.promptId === "string" ? record.promptId : undefined;
      startTurn({
        sourceTurnId,
        explicitTurnId: explicit,
        promptId,
        line,
        time: record.time,
        input: record.input,
        origin: record.origin,
        trigger: record.trigger,
      });
      continue;
    }

    if (type === "turn.steer") {
      const promptIds = Array.isArray(record.promptIds) ? record.promptIds : [];
      const firstPromptId = typeof promptIds[0] === "string" ? promptIds[0] : undefined;
      const messageId = typeof record.messageId === "string" ? record.messageId : firstPromptId;
      const explicit = Number.isInteger(record.turnId) ? record.turnId : undefined;
      const sourceTurnId = explicit ?? active?.sourceTurnId ?? nextTurnId;
      const current = segmentsBySource.get(sourceTurnId);

      if (!current) {
        // A steer with no turn to steer opens one, exactly as the host does.
        startTurn({
          sourceTurnId,
          explicitTurnId: explicit,
          promptId: messageId,
          line,
          time: record.time,
          input: record.input,
          origin: record.origin,
          trigger: "notify",
        });
        continue;
      }

      // The segment this source id pointed at is over, and it ended normally:
      // the host writes `turn.ended reason: "completed"` for it.
      closeTurn(current, "completed", line);
      // The steered input opens a fresh logical turn with its own id, mapped to
      // the same source id so the host's later `turn.ended` finds it.
      startTurn({
        sourceTurnId,
        explicitTurnId: undefined,
        promptId: messageId,
        line,
        time: record.time,
        input: record.input,
        origin: record.origin,
        trigger: "notify",
      });
      continue;
    }

    if (type === "turn.ended") {
      const explicit = Number.isInteger(record.turnId) ? record.turnId : undefined;
      const target = explicit === undefined
        ? active
        : segmentsBySource.get(explicit) ?? (active?.turnId === explicit ? active : undefined);
      if (target) closeTurn(target, record.reason, line);
      if (explicit !== undefined) {
        const closedId = target?.turnId;
        if (Number.isInteger(closedId)) nextTurnId = Math.max(nextTurnId, closedId + 1);
        if (explicit === undefined || target === active) active = null;
      }
      continue;
    }

    if (type === "turn.cancel") {
      // A cancellation is a request, not a terminal record. The host uses it to
      // skip an id in the turn clock, which is why a cancelled id is never
      // reused; the turn itself still waits for its own `turn.ended`.
      const explicit = Number.isInteger(record.turnId) ? record.turnId : undefined;
      const target = explicit === undefined ? active : segmentsBySource.get(explicit);
      if (target) target.cancelRequested = true;
      if (record.target !== undefined && explicit !== undefined && explicit >= nextTurnId) {
        cancelledTurnIds.add(explicit);
        nextTurnId = advanceClock(nextTurnId);
      }
      continue;
    }

    if (type === "context.append_loop_event") {
      if (!active) continue;
      const segment = active.segments[active.segments.length - 1];
      applyLoopEvent(active, segment, record.event, line, diagnostics);
      continue;
    }

    if (type === "context.undo") {
      if (!tree.pairedLegacyUndoLines.has(line)) diagnostics.undoneTurns += Number(record.count) || 0;
      continue;
    }

    if (type === "context.undone") {
      diagnostics.undoneTurns += Number(record.turns) || 0;
      continue;
    }

    if (type === "context.clear") {
      diagnostics.clearedTurns += turns.filter((turn) => turn.open).length;
      for (const turn of turns) {
        if (!turn.open) continue;
        turn.dropped = "context.clear";
      }
      segmentsBySource.clear();
      active = null;
      continue;
    }

    if (type === "context.apply_compaction") {
      diagnostics.compactions += 1;
      continue;
    }
  }

  const projected = turns
    .filter((turn) => !turn.dropped)
    .map((turn) => finalizeTurn(turn));

  return {
    status: "ok",
    turns: projected,
    diagnostics,
    tree,
    activeBranch: tree.activeBranch,
    // The geometry a later scan needs when its window no longer contains the
    // switches that produced it.
    edges: tree.edges.map((edge) => ({
      line: edge.line,
      branch: edge.branch,
      base: { branch: edge.base.branch, line: edge.base.line },
      ...(edge.legacyUndoLine === undefined ? {} : { legacyUndoLine: edge.legacyUndoLine }),
    })),
  };
}

/**
 * Turn the projector's working state into the shape the mapper consumes.
 *
 * `complete` means "closed by turn.ended=completed and every step inside it is
 * sealed". `terminalPartial` means closed by a non-completed reason. `open`
 * means no terminal record has been seen.
 */
function finalizeTurn(turn) {
  const segments = turn.segments.map((segment) => {
    // The segment is a list of entries in the order the model saw them: each
    // step's assistant message, then the tool results that arrived while that
    // step was open. Emitting all messages and then all results would claim the
    // model saw its own text before the tool output it was waiting on.
    const entries = [];
    const messages = [];
    const toolResults = [];

    for (const step of segment.steps) {
      const textParts = [];
      const toolParts = [];
      for (const part of step.parts) {
        if (part.type === "text") textParts.push(part);
        else if (part.type === "tool") toolParts.push(part);
      }
      if (textParts.length || toolParts.length) {
        const message = {
          kind: "assistant",
          line: step.parts.length ? step.parts[0].line : turn.startLine,
          parts: [...textParts, ...toolParts].map(({ line, ...rest }) => rest),
          sealed: step.sealed === true,
        };
        messages.push(message);
        entries.push({ kind: "assistant", message });
      }
      for (const result of step.results || []) {
        toolResults.push(result);
        entries.push({ kind: "tool", result });
      }
    }

    const userText = textFromInput(segment.input);
    return {
      index: segment.index,
      messageId: segment.messageId,
      origin: segment.origin,
      trigger: segment.trigger,
      time: segment.time,
      userText,
      messages,
      toolResults,
      entries,
      openStep: segment.openStepParts !== null,
    };
  });

  const allSealed = segments.every((segment) => !segment.openStep);
  const stepIncomplete = turn.hadOpenStep === true;
  return {
    turnId: turn.turnId,
    promptId: turn.promptId,
    startLine: turn.startLine,
    endLine: turn.endLine,
    time: turn.time,
    segments,
    ended: turn.ended,
    cancelRequested: turn.cancelRequested,
    complete: Boolean(turn.ended && turn.ended.reason === "completed" && allSealed && !stepIncomplete),
    terminal: Boolean(turn.ended),
    terminalPartial: Boolean(turn.ended && (turn.ended.reason !== "completed" || !allSealed || stepIncomplete)),
    open: !turn.ended,
    openSteps: !allSealed || stepIncomplete,
  };
}

/** Does this turn hold anything the model would have seen? */
export function turnHasContent(turn) {
  if (!turn) return false;
  return turn.segments.some((segment) => (
    segment.userText
    || segment.messages.length > 0
    || segment.toolResults.length > 0
  ));
}
