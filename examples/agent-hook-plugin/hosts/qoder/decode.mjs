/**
 * Decode Qoder CLI's transcript into stable incremental user/assistant turns.
 *
 * Qoder writes Claude Code-style JSONL (hosts/qoder/DESIGN.md §8), with two
 * differences that shape this decoder:
 *
 * 1. **One assistant record per content block.** A model response is split
 *    into `text` / `tool_use` / `thinking` records that share a
 *    `responseGroupId`. They are folded back into one assistant turn, in record
 *    order, together with the `tool_result` user records that answer its calls;
 *    `thinking` is dropped. A lazily loaded MCP tool is called through
 *    `mcp_call`; the call is unwrapped to the tool it names.
 *
 * 2. **Host context is written to the transcript.** What a hook injects lands
 *    in `attachment` records (`hook_output`, `hook_success`), beside the skill
 *    listing, environment and session context. Only `user` and `assistant`
 *    records are read, so the recalled memories this plugin injects are never
 *    captured back.
 *
 * A `user` record is a human ask only when it carries text, is not host-authored
 * (`isMeta` skill bodies, the `isCompactSummary` summary, a non-human
 * `origin.kind`) and is not a subagent's (`isSidechain`). Tool results arrive as
 * `user` records too and stay in the assistant turn they answer.
 *
 * The turn's stable id is the index of the record that opened it: the
 * transcript is append-only, compaction and resume included, so the position
 * of a turn is a durable cursor.
 */

import { parseJsonl, resumeTurns, turnsFromBlocks } from "../../lib/flow/turns.mjs";

function contentBlocks(record) {
  const content = record?.message?.content;
  if (typeof content === "string") return content.trim() ? [{ type: "text", text: content }] : [];
  return Array.isArray(content) ? content.filter((block) => block && typeof block === "object") : [];
}

function isText(block) {
  return block.type === "text" && typeof block.text === "string" && block.text.trim() !== "";
}

// Lazy MCP loading calls every MCP tool as `mcp_call` (DESIGN.md §9); the part
// names the real tool, so its result pairs with it and OpenViking tools are known.
function unwrapMcpCall(block) {
  const { toolName, arguments: args } = block.input || {};
  if (block.name !== "mcp_call" || typeof toolName !== "string" || !toolName) return block;
  return { ...block, name: toolName, input: args ?? {} };
}

function isHostAuthoredUser(record) {
  if (record.isMeta === true || record.isCompactSummary === true) return true;
  const kind = record.origin?.kind;
  return kind != null && kind !== "human";
}

/** Qoder records → what the turn aggregation needs, or null for host records. */
function decodeRecord(record) {
  if (record?.isSidechain === true) return null;
  const blocks = contentBlocks(record);
  if (record?.type === "user") {
    const results = blocks.filter((block) => block.type === "tool_result");
    if (results.length > 0) return { kind: "tool", blocks: results };
    if (isHostAuthoredUser(record)) return null;
    const texts = blocks.filter(isText);
    return texts.length > 0 ? { kind: "user", blocks: texts } : null;
  }
  if (record?.type === "assistant") {
    const kept = blocks
      .filter((block) => isText(block) || block.type === "tool_use")
      .map((block) => (block.type === "tool_use" ? unwrapMcpCall(block) : block));
    if (kept.length === 0) return null;
    return { kind: "assistant", group: record.responseGroupId || null, blocks: kept };
  }
  return null;
}

export function extractQoderTurns(records) {
  const collected = [];
  let current = null;
  const open = (role, index, group = null) => {
    current = { role, group, blocks: [], recordIndex: index };
    collected.push(current);
  };

  (records || []).forEach((record, index) => {
    const decoded = decodeRecord(record);
    if (!decoded) return;
    if (decoded.kind === "user") {
      open("user", index);
    } else if (decoded.kind === "assistant") {
      // A new model response opens a new turn; its other blocks, and the tool
      // results answering it, stay in that turn.
      if (current?.role !== "assistant" || (decoded.group && current.group !== decoded.group)) {
        open("assistant", index, decoded.group);
      }
    } else if (current?.role !== "assistant") {
      open("assistant", index);
    }
    current.blocks.push(...decoded.blocks);
  });
  return turnsFromBlocks(collected);
}

/** @param {import("../../lib/flow/host.d.mts").DecodeInput} input */
export function decode({ text, cursor }) {
  return resumeTurns(extractQoderTurns(parseJsonl(text)), cursor);
}
