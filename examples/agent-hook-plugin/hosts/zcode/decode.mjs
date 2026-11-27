/**
 * ZCode turns, read from its rollout file.
 *
 * ZCode records every model call of a session in
 * ~/.zcode/cli/rollout/model-io-<sessionId>.jsonl, one `model_io` line per
 * call, each carrying the host `turnId` of the user prompt it serves:
 *   { turnId, querySource, error?,
 *     request: { messagesKind, messageOffset, messages: [{ role, content, toolCalls?, toolCallId?, toolName?, isError? }] },
 *     response: { text, toolCalls: [{ id, name, input }] } }
 * `request.messages` may be the whole conversation, its tail or only what is
 * new since the previous line, so a turn is assembled from what each line
 * adds: its response (text and tool calls) and the `tool` messages that answer
 * calls made earlier in the same turn. The prompt is the newest user message
 * of the turn's first line. Lines of other query sources (compaction,
 * subagents, web fetch) are not the conversation.
 */

import { sanitizeCapturedText } from "../../lib/capture-utils.mjs";
import { parseJsonl, resumeTurns } from "../../lib/flow/turns.mjs";

function messageText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((block) => block?.type === "text" && typeof block.text === "string")
    .map((block) => block.text)
    .join("\n");
}

// ZCode puts its own reminders and the hooks' additional context in user
// messages of their own, so the prompt is the newest user message that still
// has text once those are stripped. Anything past an assistant or tool message
// belongs to an earlier turn.
function promptOf(messages) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message?.role !== "user") return "";
    const text = messageText(message.content);
    if (sanitizeCapturedText(text)) return text;
  }
  return "";
}

export function extractZcodeTurns(records) {
  const turns = new Map();
  for (const record of records) {
    if (record.type && record.type !== "model_io") continue;
    if (record.querySource && record.querySource !== "main_turn") continue;
    const turnId = typeof record.turnId === "string" ? record.turnId : "";
    if (!turnId) continue;
    const messages = Array.isArray(record.request?.messages) ? record.request.messages : [];
    let turn = turns.get(turnId);
    if (!turn) {
      turn = { prompt: promptOf(messages), blocks: [], calls: new Set(), answered: new Set() };
      turns.set(turnId, turn);
    }
    for (const message of messages) {
      const id = message?.role === "tool" ? message.toolCallId : "";
      if (!id || !turn.calls.has(id) || turn.answered.has(id)) continue;
      turn.answered.add(id);
      turn.blocks.push({
        type: "tool_result",
        tool_use_id: id,
        name: message.toolName,
        content: message.content ?? "",
        is_error: message.isError === true,
      });
    }
    // A failed attempt is retried on a line of its own.
    if (record.error || !record.response) continue;
    if (typeof record.response.text === "string" && record.response.text.trim()) {
      turn.blocks.push({ type: "text", text: record.response.text });
    }
    for (const call of Array.isArray(record.response.toolCalls) ? record.response.toolCalls : []) {
      if (!call?.id || turn.calls.has(call.id)) continue;
      turn.calls.add(call.id);
      turn.blocks.push({ type: "tool_use", id: call.id, name: call.name, input: call.input ?? {} });
    }
  }

  const out = [];
  for (const [turnId, { prompt, blocks }] of turns) {
    if (prompt) out.push({ role: "user", content: prompt, turnId });
    if (blocks.length === 0) continue;
    const assistant = {
      role: "assistant",
      content: blocks.filter((block) => block.type === "text").map((block) => block.text).join("\n\n"),
      turnId,
    };
    if (blocks.some((block) => block.type !== "text")) assistant.payload = { role: "assistant", content: blocks };
    out.push(assistant);
  }
  return out;
}

// Without the rollout file the Stop payload's reply and the prompt noted at
// UserPromptSubmit are the turn, as text.
function payloadTurns(payload, notes) {
  const reply = payload.responseText || payload.responsePreview || payload.last_assistant_message
    || payload.assistantMessage || payload.assistant_message || payload.text_content || payload.response || "";
  const prompt = payload.prompt || payload.user_prompt || payload.userMessage || payload.user_message
    || payload.last_user_message || payload.message || notes.findLast((note) => note.prompt)?.prompt || "";
  return [{ role: "user", content: prompt }, { role: "assistant", content: reply }]
    .filter((turn) => typeof turn.content === "string" && sanitizeCapturedText(turn.content));
}

/** @param {import("../../lib/flow/host.d.mts").DecodeInput} input */
export function decode({ text, cursor, notes = [], payload = {} }) {
  return resumeTurns(text ? extractZcodeTurns(parseJsonl(text)) : payloadTurns(payload, notes), cursor);
}
