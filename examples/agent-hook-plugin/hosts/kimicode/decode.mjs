/**
 * Decode Kimi Code's wire log (`agents/main/wire.jsonl`, hosts/kimicode/DESIGN.md)
 * into user/assistant turns keyed by Kimi's own `turnId`, which the user prompt
 * and the assistant side of one turn share.
 */

import { sanitizeCapturedText } from "../../lib/capture-utils.mjs";
import { parseJsonl, resumeTurns } from "../../lib/flow/turns.mjs";

const partText = (part) => (typeof part === "string" ? part : typeof part?.text === "string" ? part.text : "");

export function cleanKimicodeText(value) {
  if (value == null) return "";
  const text = Array.isArray(value)
    ? value.map(partText).filter(Boolean).join("\n")
    : typeof value === "object" && typeof value.text === "string"
      ? value.text
      : String(value);
  return sanitizeCapturedText(text);
}

// Kimi files a UserPromptSubmit hook's output and an activated skill's body as
// text parts of the user's own message, tagged with these sources.
const HOST_PART_SOURCES = new Set(["user prompt submit hook", "skill activation"]);

function textFromContent(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => !HOST_PART_SOURCES.has(part?.meta?.source))
    .map(partText)
    .filter(Boolean)
    .join("\n");
}

function toolOutputText(output) {
  if (typeof output === "string") return output;
  return Array.isArray(output) ? output.map(partText).filter(Boolean).join("\n") : null;
}

// Kimi names a plugin's MCP tool `mcp__plugin-<plugin>_<server>__<tool>`; the
// server recognises its own tools only as `mcp__openviking__<tool>`.
const OPENVIKING_MCP_TOOL = /^mcp__(?:plugin-[^_]+_)?openviking__(?=.)/;
const toolName = (name) => name.replace(OPENVIKING_MCP_TOOL, "mcp__openviking__");

const idOf = (value) => (typeof value === "string" || typeof value === "number" ? String(value) : null);

export function extractKimicodeTurns(records) {
  const users = new Map();
  const assistants = new Map();
  // Per turn, the assistant side in wire order: text, tool calls and results.
  const blocks = new Map();
  const toolTurns = new Map();
  const stepTurns = new Map();
  const order = [];
  let pendingUser = "";
  const attachPending = (value) => {
    const turnId = String(value ?? order.length);
    if (!order.includes(turnId)) order.push(turnId);
    if (pendingUser) {
      users.set(turnId, users.has(turnId) ? `${users.get(turnId)}\n${pendingUser}` : pendingUser);
      pendingUser = "";
    }
    return turnId;
  };
  const pushBlock = (turnId, block) => {
    if (!blocks.has(turnId)) blocks.set(turnId, []);
    blocks.get(turnId).push(block);
  };

  for (const row of records) {
    if (row.type === "context.append_message" && row.message?.role === "user") {
      // Origin kind `injection` is a host reminder.
      const kind = row.message.origin?.kind;
      if (!kind || kind === "user") pendingUser = textFromContent(row.message.content) || pendingUser;
      continue;
    }
    if (row.type === "turn.prompt") {
      if (!pendingUser) pendingUser = textFromContent(row.input);
      continue;
    }
    // `turn.ended` closes interrupted or tool-only turns.
    if (row.type === "turn.ended") {
      attachPending(row.turnId ?? row.event?.turnId);
      continue;
    }
    if (row.type !== "context.append_loop_event") continue;
    const event = row.event && typeof row.event === "object" ? row.event : {};
    const stepTurn = idOf(event.turnId);
    if (stepTurn !== null && typeof (event.stepUuid ?? event.uuid) === "string") {
      stepTurns.set(event.stepUuid ?? event.uuid, stepTurn);
    }
    if (event.type === "turn.ended") {
      attachPending(event.turnId ?? row.turnId);
      continue;
    }
    if (event.type === "content.part" && event.part?.type === "text") {
      const turnId = attachPending(event.turnId);
      const text = event.part.text || "";
      const prev = assistants.get(turnId);
      assistants.set(turnId, prev ? `${prev}\n${text}` : text);
      if (text) pushBlock(turnId, { type: "text", text });
      continue;
    }
    if (event.type === "tool.call") {
      const id = idOf(event.toolCallId);
      // A call without `turnId` takes the one its step carried.
      const turn = stepTurn ?? stepTurns.get(event.stepUuid);
      if (!id || typeof event.name !== "string" || !event.name || !turn) continue;
      const turnId = attachPending(turn);
      toolTurns.set(id, turnId);
      pushBlock(turnId, {
        type: "tool_use",
        id,
        name: toolName(event.name),
        ...(event.args === undefined ? {} : { input: event.args }),
      });
      continue;
    }
    if (event.type === "tool.result") {
      // A result has no `turnId`: it joins the turn of the call it answers.
      const id = idOf(event.toolCallId);
      const turnId = id ? toolTurns.get(id) : undefined;
      const output = toolOutputText(event.result?.output);
      if (!turnId || output === null) continue;
      toolTurns.delete(id);
      pushBlock(turnId, {
        type: "tool_result",
        tool_use_id: id,
        output,
        ...(event.result.isError === true ? { is_error: true } : {}),
      });
    }
  }

  const turns = [];
  for (const turnId of order) {
    const user = cleanKimicodeText(users.get(turnId) || "");
    const assistant = cleanKimicodeText(assistants.get(turnId) || "");
    const assistantBlocks = blocks.get(turnId) || [];
    if (user) turns.push({ role: "user", content: user, turnId });
    if (assistantBlocks.some((block) => block.type !== "text")) {
      turns.push({ role: "assistant", content: assistant, turnId, payload: { role: "assistant", content: assistantBlocks } });
    } else if (assistant) {
      turns.push({ role: "assistant", content: assistant, turnId });
    }
  }
  return turns;
}

/** @param {import("../../lib/flow/host.d.mts").DecodeInput} input */
export function decode({ text, cursor }) {
  return resumeTurns(extractKimicodeTurns(parseJsonl(text)), cursor);
}
