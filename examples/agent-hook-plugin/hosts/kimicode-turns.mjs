/** Decode Kimi Code's wire log into stable incremental user/assistant turns. */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { sanitizeCapturedText } from "../../memory-plugin-shared/lib/capture-utils.mjs";

export function cleanKimicodeText(value) {
  if (value == null) return "";
  const text = Array.isArray(value)
    ? value.map((part) => (typeof part === "string" ? part : part?.text || "")).filter(Boolean).join("\n")
    : typeof value === "object" && typeof value.text === "string"
      ? value.text
      : String(value);
  return sanitizeCapturedText(text);
}

function kimiCodeHome() {
  return process.env.KIMI_CODE_HOME || join(homedir(), ".kimi-code");
}

// Kimi files a UserPromptSubmit hook's output and an activated skill's body as
// text parts of the user's own message, tagged with these sources.
const HOST_PART_SOURCES = new Set(["user prompt submit hook", "skill activation"]);

function textFromContent(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((part) => !HOST_PART_SOURCES.has(part?.meta?.source))
    .map((part) => (typeof part === "string" ? part : part?.text || ""))
    .filter(Boolean)
    .join("\n");
}

export function resolveKimicodeWirePath(input = {}, home = kimiCodeHome()) {
  const sessionId = String(input.session_id || "").trim();
  if (!sessionId) return "";
  const indexPath = join(home, "session_index.jsonl");
  if (existsSync(indexPath)) {
    try {
      for (const line of readFileSync(indexPath, "utf8").split("\n")) {
        if (!line.trim()) continue;
        const row = JSON.parse(line);
        if (row.sessionId === sessionId && row.sessionDir) {
          return join(row.sessionDir, "agents", "main", "wire.jsonl");
        }
      }
    } catch {
      // Fall back to the session directory scan.
    }
  }
  const sessionsRoot = join(home, "sessions");
  if (!existsSync(sessionsRoot)) return "";
  try {
    for (const cwdKey of readdirSync(sessionsRoot)) {
      const candidate = join(sessionsRoot, cwdKey, sessionId, "agents", "main", "wire.jsonl");
      if (existsSync(candidate)) return candidate;
    }
  } catch {
    return "";
  }
  return "";
}

function toolOutputText(output) {
  if (typeof output === "string") return output;
  if (!Array.isArray(output)) return null;
  return output
    .map((part) => (typeof part === "string" ? part : typeof part?.text === "string" ? part.text : ""))
    .filter(Boolean)
    .join("\n");
}

// Kimi names a plugin's MCP tool `mcp__plugin-<plugin>_<server>__<tool>`; the
// server recognises its own tools only as `mcp__openviking__<tool>`.
const OPENVIKING_MCP_TOOL = /^mcp__(?:plugin-[^_]+_)?openviking__(?=.)/;
const toolName = (name) => name.replace(OPENVIKING_MCP_TOOL, "mcp__openviking__");

const turnKey = (value) => (typeof value === "string" || typeof value === "number" ? String(value) : null);

export function extractUnseenKimicodeTurns(wirePath, lastTurnId = null) {
  if (!wirePath || !existsSync(wirePath)) return { status: "missing", turns: [] };
  let raw;
  try {
    raw = readFileSync(wirePath, "utf8");
  } catch (error) {
    return { status: "unreadable", turns: [], error: error?.code || "read_failed" };
  }

  const users = new Map();
  const assistants = new Map();
  // Per turn, the assistant side in wire order: text, tool calls and results.
  const blocks = new Map();
  const toolTurns = new Map();
  const stepTurns = new Map();
  const toolNameById = {};
  const order = [];
  let pendingUser = "";
  const remember = (value) => {
    const turnId = String(value);
    if (!order.includes(turnId)) order.push(turnId);
    return turnId;
  };
  const attachPending = (value) => {
    const turnId = remember(value ?? order.length);
    if (pendingUser) {
      users.set(turnId, `${users.get(turnId) || ""}${users.has(turnId) ? "\n" : ""}${pendingUser}`);
      pendingUser = "";
    }
    return turnId;
  };
  const pushBlock = (turnId, block) => {
    if (!blocks.has(turnId)) blocks.set(turnId, []);
    blocks.get(turnId).push(block);
  };

  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    let row;
    try {
      row = JSON.parse(line);
    } catch {
      continue;
    }
    if (!row || typeof row !== "object") continue;
    if (row.type === "context.append_message" && row.message?.role === "user") {
      const kind = row.message.origin?.kind;
      if (!kind || kind === "user") pendingUser = textFromContent(row.message.content) || pendingUser;
      continue;
    }
    if (row.type === "turn.prompt") {
      if (!pendingUser) pendingUser = textFromContent(row.input);
      continue;
    }
    if (row.type === "turn.ended") {
      attachPending(row.turnId ?? row.event?.turnId);
      continue;
    }
    if (row.type !== "context.append_loop_event") continue;
    const event = row.event && typeof row.event === "object" ? row.event : {};
    const stepTurn = turnKey(event.turnId);
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
      const id = turnKey(event.toolCallId);
      const turn = stepTurn ?? stepTurns.get(event.stepUuid);
      if (!id || typeof event.name !== "string" || !event.name || !turn) continue;
      const turnId = attachPending(turn);
      toolTurns.set(id, turnId);
      toolNameById[id] = toolName(event.name);
      pushBlock(turnId, {
        type: "tool_use",
        id,
        name: toolNameById[id],
        ...(event.args === undefined ? {} : { input: event.args }),
      });
      continue;
    }
    if (event.type === "tool.result") {
      const id = turnKey(event.toolCallId);
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
  const lastIndex = lastTurnId == null || lastTurnId === ""
    ? -1
    : order.indexOf(String(lastTurnId));
  for (const turnId of order.slice(lastIndex + 1)) {
    const user = cleanKimicodeText(users.get(turnId) || "");
    const assistant = cleanKimicodeText(assistants.get(turnId) || "");
    const assistantBlocks = blocks.get(turnId) || [];
    const hasTools = assistantBlocks.some((block) => block.type !== "text");
    if (user) turns.push({ role: "user", content: user, turnId });
    if (hasTools) {
      turns.push({
        role: "assistant",
        content: assistant,
        turnId,
        payload: { role: "assistant", content: assistantBlocks },
      });
    } else if (assistant) {
      turns.push({ role: "assistant", content: assistant, turnId });
    }
  }
  return { status: "ok", turns, toolNameById };
}

export function buildKimicodeTurns(input = {}, state = {}) {
  return extractUnseenKimicodeTurns(
    resolveKimicodeWirePath(input),
    state.lastTurnId || null,
  );
}
