/**
 * Map projected Kimi turns onto OpenViking session messages.
 *
 * The wire is Kimi's; the message shape is OpenViking's. This module is the
 * only place that knows both, and it deliberately maps *evidence*, not prose:
 * the user's text, each assistant step's ordered text, the tool call that
 * produced each result, and the result itself. Everything the model did not
 * see — thinking parts, compaction summaries, permission prompts, goal state —
 * is dropped upstream in the projector.
 *
 * Three host facts drive the shape:
 *
 * - A tool result is a *user-role* message with `message_kind=tool_transport`,
 *   linked to its call by `tool_id`. It is not an assistant message, and it is
 *   not merged into the call's text.
 * - A steer splits one native turn into several logical segments. Each segment
 *   gets its own `turn_id` so the server can order and attribute them, while
 *   the native turn stays one unit for commit accounting.
 * - Timestamps arrive as epoch milliseconds and leave as ISO strings.
 *
 * Message ids are never set here, and `peer_id` is stamped by the shared sender.
 */

import { shapeCapturePayload } from "./shared/capture-utils.mjs";
import { applyInputFilters, compileInputFilters } from "./shared/input-filters.mjs";

/**
 * Which user inputs become `user_query`.
 *
 * This is the host's own visibility rule (`isVisibleWireTurnInput` in
 * `wire/replayBuilder/fold.ts`), not an approximation of it:
 *
 *   origin.kind=user                            visible
 *   skill_activation / plugin_command           visible only when trigger is
 *                                               "user-slash", and then as the
 *                                               command the user typed, not the
 *                                               skill body
 *   shell_command                               visible only at phase "input"
 *   anything else (task, system_trigger,
 *   injection, hook_result)                     not a user question
 *
 * `trigger === "continue"` is the host continuing on its own and is never a
 * user input, whatever the origin says.
 */
export function classifyUserInput({ origin, trigger } = {}) {
  if (trigger === "continue") return { visible: false, reason: "continue" };
  const kind = origin?.kind;
  if (kind === undefined || kind === null) {
    // A pre-origin record is the host's oldest format and has always been a
    // real user message.
    return { visible: true, kind: "user" };
  }
  if (kind === "user") return { visible: true, kind: "user" };
  if (kind === "skill_activation" || kind === "plugin_command") {
    return origin?.trigger === "user-slash"
      ? { visible: true, kind, slash: true }
      : { visible: false, reason: kind };
  }
  if (kind === "shell_command") {
    return origin?.phase === "input"
      ? { visible: true, kind }
      : { visible: false, reason: "shell_command_phase" };
  }
  return { visible: false, reason: typeof kind === "string" ? kind : "unknown_origin" };
}

/**
 * The text a slash command was visible as.
 *
 * The host records the activation and the command the user typed; the skill's
 * own instructions are a separate body that must not become the user's
 * question. The wire carries the command in the origin, in the host's own
 * field names — `skillName`/`skillArgs` for a skill, `pluginId`/`commandName`/
 * `commandArgs` for a plugin command — and the host renders them as
 * `/<skill> <args>` and `/<pluginId>:<commandName> <args>`. Reconstructing
 * from those is what keeps a real slash command in the transcript instead of
 * dropping it for having no text.
 */
export function slashCommandText({ origin, input }) {
  const typed = typeof input === "string" ? input : "";
  if (typed.trim()) return typed;
  const args = typeof origin?.skillArgs === "string" && origin.skillArgs.trim()
    ? ` ${origin.skillArgs.trim()}`
    : (typeof origin?.commandArgs === "string" && origin.commandArgs.trim() ? ` ${origin.commandArgs.trim()}` : "");

  if (origin?.kind === "plugin_command") {
    // `pluginId`/`commandName` are the host's fields; `plugin`/`name` are kept
    // for the shapes other callers and older fixtures use.
    const plugin = String(origin?.pluginId ?? origin?.plugin ?? "");
    const name = String(origin?.commandName ?? origin?.name ?? "");
    if (!name) return "";
    return plugin ? `/${plugin}:${name}${args}` : `/${name}${args}`;
  }

  const skill = String(origin?.skillName ?? origin?.name ?? "");
  if (!skill) return "";
  return `/${skill}${args}`;
}

function toIso(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  // The wire writes epoch milliseconds. A seconds value would be from a much
  // older host and would land in 1970, so it is rejected rather than guessed —
  // the comment above said so while the code accepted it.
  if (value < 1e12) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toISOString();
}

function toolPartToPart(part) {
  const out = { type: "tool", tool_id: part.tool_id, tool_status: part.tool_status || "running" };
  if (part.tool_name) out.tool_name = part.tool_name;
  if (part.tool_input !== undefined) out.tool_input = part.tool_input;
  if (part.tool_output !== undefined) out.tool_output = part.tool_output;
  return out;
}

/**
 * One projected turn into a list of OpenViking message payloads.
 *
 * `kind` decides how much of the turn may be sent:
 *   complete         the whole turn, ending with its tool results
 *   terminalPartial  what exists; never a synthesized result or closing text
 *   recoveryPartial  the same, but the caller records why
 *
 * Returns `{ payloads, stableIds, dropped, reason }`. `stableIds` are the
 * per-message identities the capture stage keeps so a re-read cannot deliver
 * the same message twice; they are derived from wire facts (turn, segment,
 * step index, tool call id), never from text.
 */
export function mapTurnToMessages(turn, cfg = {}, {
  ovSessionId = "",
  partial = false,
  receivedAt = undefined,
} = {}) {
  const payloads = [];
  const stableIds = [];
  const dropped = [];

  // The shared capture filters take one drop verdict per turn, and a turn
  // dropped on its text takes its tool evidence with it. Doing that here, once
  // for the whole turn, is what keeps this adapter from inventing a second
  // rule: a tool-only assistant step would otherwise survive a turn whose text
  // the user configured to be dropped.
  const verdict = turnFilterVerdict(turn, cfg);
  if (verdict.dropped) {
    return {
      payloads: [],
      stableIds: [],
      dropped: [{ kind: "turn", reason: "filtered", ruleIndex: verdict.ruleIndex }],
      partial,
      ovSessionId,
    };
  }

  for (const segment of turn.segments || []) {
    // A logical turn has one id, assigned by the host's own clock. A steer
    // starts a new logical turn rather than extending this one, so the id is
    // simply this turn's; a segment index would invent an identity the host
    // never assigned.
    const segmentTurnId = logicalTurnIdFor(turn.turnId, 0, turn.startLine);

    // ---- user input -------------------------------------------------------
    const verdict = classifyUserInput(segment);
    if (verdict.visible) {
      const text = verdict.slash ? slashCommandText(segment) : segment.userText;
      if (text && text.trim()) {
        const payload = {
          role: "user",
          parts: [{ type: "text", text }],
          message_kind: "user_query",
          turn_id: segmentTurnId,
        };
        const createdAt = toIso(segment.time) || receivedAt;
        if (createdAt) payload.created_at = createdAt;
        const shaped = shapePayload(payload, "user", cfg);
        if (shaped) {
          payloads.push(shaped);
          stableIds.push(stableId(turn, segment, "user", 0));
        } else {
          dropped.push({ kind: "user", reason: shaped === null ? "filtered" : "empty" });
        }
      }
    } else {
      dropped.push({ kind: "user", reason: verdict.reason });
    }

    // ---- assistant steps and their tool results, in the order they happened --
    // The projection already interleaves them per step, because that is the
    // order the model saw: it wrote a step, the tool answered, it wrote the
    // next step. Emitting every assistant message and then every result would
    // describe a conversation that never happened.
    let stepIndex = 0;
    for (const entry of segment.entries || []) {
      if (entry.kind === "assistant") {
        const parts = entry.message.parts.map((part) => (part.type === "tool" ? toolPartToPart(part) : part));
        if (parts.length > 0) {
          const payload = {
            role: "assistant",
            parts,
            message_kind: "assistant_step",
            turn_id: segmentTurnId,
          };
          const createdAt = toIso(entry.message.time ?? segment.time) || receivedAt;
          if (createdAt) payload.created_at = createdAt;
          const shaped = shapePayload(payload, "assistant", cfg);
          if (shaped) {
            payloads.push(shaped);
            stableIds.push(stableId(turn, segment, "assistant", stepIndex));
          } else {
            // A step whose text hits a capture drop rule takes its tool parts
            // with it — the shared filter decides that, and this adapter does
            // not re-implement the rule.
            dropped.push({ kind: "assistant", reason: "filtered" });
          }
        }
        stepIndex += 1;
        continue;
      }

      const result = entry.result;
      const payload = {
        role: "user",
        parts: [{
          type: "tool",
          tool_id: result.tool_id,
          tool_name: result.tool_name,
          tool_output: result.tool_output,
          tool_status: result.isError ? "error" : "completed",
        }],
        message_kind: "tool_transport",
        turn_id: segmentTurnId,
      };
      // Only a real duration is reported; a missing one is not zero.
      if (typeof result.durationMs === "number") payload.parts[0].duration_ms = result.durationMs;
      const createdAt = toIso(result.time ?? segment.time) || receivedAt;
      if (createdAt) payload.created_at = createdAt;
      const shaped = shapePayload(payload, "user", cfg);
      if (shaped) {
        payloads.push(shaped);
        stableIds.push(stableId(turn, segment, `tool:${result.tool_id}`, 0));
      } else {
        dropped.push({ kind: "tool", reason: "filtered" });
      }
    }
  }

  return { payloads, stableIds, dropped, partial, ovSessionId };
}

/**
 * Turn one of our parts into the host-native block the shared shaper expects.
 *
 * `shapeCapturePayload`/`extractPartsFromContent` classify blocks by type, and
 * an OpenViking `{type:"tool"}` part matches their *result* branch. So the
 * shaper is fed `tool_call`/`tool_result` blocks — the shapes it knows — and the
 * shaped output is converted back below.
 */
function toShapeBlock(part) {
  if (part.type !== "tool") return part;
  if (part.tool_status === "running") {
    return {
      type: "tool_call",
      tool_call_id: part.tool_id,
      name: part.tool_name,
      input: part.tool_input,
    };
  }
  return {
    type: "tool_result",
    tool_call_id: part.tool_id,
    name: part.tool_name,
    output: part.tool_output,
    is_error: part.tool_status === "error",
  };
}

/** Convert a shaped part back, restoring the fields the shaper does not carry. */
function fromShapedPart(part, originals) {
  if (part.type !== "tool") return part;
  const original = originals.find((item) => item.tool_id === part.tool_id);
  if (!original) return part;
  const out = { ...part };
  if (original.tool_status) out.tool_status = original.tool_status;
  if (original.duration_ms !== undefined) out.duration_ms = original.duration_ms;
  return out;
}

function shapePayload(payload, role, cfg) {
  // `shapeCapturePayload` reads the host-shaped `content` array: it is the one
  // place that knows how to sanitize, filter and cap a message, and every
  // harness feeds it the shape its own transcript uses. Feeding it `parts`
  // would silently shape nothing at all — every message would come back empty.
  const shaped = shapeCapturePayload(
    { role, content: payload.parts.map(toShapeBlock) },
    role,
    cfg,
  );
  if (shaped.dropped) return null;

  // `shaped.text` is the shaper's verdict on the text: it carries the
  // acknowledgement / too-short / slash-command drop, the capture filters and
  // the `captureMaxLength` cap. Rebuilding the text part from it is what makes
  // those decisions real; keeping the original text parts would let "ok" and a
  // 200 KB paste through untouched.
  //
  // Order is preserved by rebuilding in place: each original text part becomes
  // one text part of the shaped text, and tool parts stay where they were. The
  // text is not collapsed into a single leading block, which would reorder a
  // step whose tool call came first.
  const toolParts = (shaped.parts || [])
    .filter((part) => part.type === "tool")
    .map((part) => fromShapedPart(part, payload.parts));
  // The shaper always renders some `text`: for a message with no text part it
  // synthesizes `[tool-call Read] {...}`, which is a summary for a human, not
  // something the model said. Only a message that *had* text may carry it.
  const originalHasText = payload.parts.some((part) => part.type !== "tool");
  const text = originalHasText ? shaped.text : "";
  // `shaped.parts` keeps the original text so a caller can inspect it; the
  // *decision* is `shaped.text`, which is empty when the shared rules said this
  // text should not be captured at all.
  if (!text && toolParts.length === 0) return null;

  const parts = [];
  let textPlaced = false;
  for (const original of payload.parts) {
    if (original.type !== "tool") {
      // The shaper returns one text value for the whole message; it is placed
      // at the first text position, which is where the model saw it start.
      if (!textPlaced && text) {
        parts.push({ type: "text", text });
        textPlaced = true;
      }
      continue;
    }
    const shapedTool = toolParts.find((part) => part.tool_id === original.tool_id);
    if (shapedTool) parts.push(shapedTool);
  }
  if (!textPlaced && text) parts.unshift({ type: "text", text });
  if (parts.length === 0) return null;
  return { ...payload, parts };
}

function stableId(turn, segment, kind, index) {
  return [
    turn.turnId !== undefined ? `t${turn.turnId}` : `l${turn.startLine}`,
    `s${segment.index}`,
    segment.messageId ? `m${segment.messageId}` : "",
    kind,
    String(index),
  ].filter(Boolean).join(":");
}

/**
 * The shared capture filters' verdict for the whole turn.
 *
 * One drop decision per turn, on the aggregate of its text parts, exactly as
 * `filterCaptureParts` does for a single message. A rule that drops a turn
 * drops its tool evidence too, which is the shared semantics; a rule that only
 * substitutes is left to the per-message shaping.
 */
function turnFilterVerdict(turn, cfg) {
  const compiled = compileInputFilters(cfg?.captureFilters);
  if (!compiled.rules.length) return { dropped: false };

  const texts = [];
  for (const segment of turn.segments || []) {
    if (segment.userText) texts.push(segment.userText);
    for (const message of segment.messages || []) {
      for (const part of message.parts || []) {
        if (part.type === "text" && part.text) texts.push(part.text);
      }
    }
  }
  if (texts.length === 0) return { dropped: false };
  const verdict = applyInputFilters(texts.join("\n\n"), compiled.rules, { role: "user" });
  return { dropped: verdict.dropped, ruleIndex: verdict.ruleIndex };
}

/**
 * The `turn_id` the server sees.
 *
 * The host already assigns one id per logical turn, and a steer creates a new
 * logical turn rather than a new segment of the old one. So this is the id the
 * projection produced; nothing is appended to it. A turn without an id (a wire
 * that predates them) falls back to its start line, which is stable across
 * re-reads of the same file.
 */
export function logicalTurnIdFor(turnId, segmentIndex = 0, startLine = 0) {
  if (turnId !== undefined && turnId !== null) return String(turnId);
  return segmentIndex > 0 ? `l${startLine}-${segmentIndex}` : `l${startLine}`;
}
