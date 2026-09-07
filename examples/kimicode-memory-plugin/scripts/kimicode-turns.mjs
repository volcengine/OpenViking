/**
 * Pure transcript parser for Kimi Code CLI hook events.
 *
 * Verified against Kimi Code 0.29.2 docs and a live ~/.kimi-code install:
 *   - Hook stdin is snake_case JSON (session_id, cwd, hook_event_name).
 *   - The authoritative incremental transcript is the session wire log:
 *     $KIMI_CODE_HOME/sessions/<wd>/session_<id>/agents/main/wire.jsonl
 *     indexed by ~/.kimi-code/session_index.jsonl.
 *   - User turns: `turn.prompt` / `context.append_message` (role=user).
 *   - Assistant turns: `context.append_loop_event` → content.part type=text
 *     grouped by event.turnId. Think parts are ignored.
 *
 * Strategy:
 * 1. Prefer wire.jsonl and emit unseen turns after state.lastTurnId.
 * 2. Fall back to hook stdin + pendingPrompt only when the wire file is missing.
 */

import { existsSync, readFileSync, readdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const INJECTION_BLOCKS = [
  /<openviking-context\b[^>]*>[\s\S]*?<\/openviking-context>/gi,
  /<relevant-memories\b[^>]*>[\s\S]*?<\/relevant-memories>/gi,
  /<relevant-memory\b[^>]*>[\s\S]*?<\/relevant-memory>/gi,
  /<system-reminder\b[^>]*>[\s\S]*?<\/system-reminder>/gi,
];

export function cleanKimicodeText(value) {
  if (value == null) return "";
  let text = String(value);
  for (const pattern of INJECTION_BLOCKS) {
    text = text.replace(pattern, "");
  }
  return text.replace(/\s+\n/g, "\n").trim();
}

export function kimiCodeHome() {
  return process.env.KIMI_CODE_HOME || join(homedir(), ".kimi-code");
}

function textFromContent(content) {
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => {
      if (typeof part === "string") return part;
      if (part && typeof part.text === "string") return part.text;
      return "";
    })
    .filter(Boolean)
    .join("\n");
}

function sessionIdOf(input = {}) {
  return String(input.session_id || input.sessionId || "").trim();
}

export function resolveWirePath(input = {}, home = kimiCodeHome()) {
  const sessionId = sessionIdOf(input);
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
      // Fall through to directory scan.
    }
  }
  const sessionsRoot = join(home, "sessions");
  if (!existsSync(sessionsRoot)) return "";
  try {
    for (const wd of readdirSync(sessionsRoot)) {
      const candidate = join(sessionsRoot, wd, sessionId, "agents", "main", "wire.jsonl");
      if (existsSync(candidate)) return candidate;
    }
  } catch {
    return "";
  }
  return "";
}

export function extractUnseenWireTurns(wirePath, lastTurnId = null) {
  if (!wirePath || !existsSync(wirePath)) {
    return { available: false, turns: [] };
  }
  let raw = "";
  try {
    raw = readFileSync(wirePath, "utf8");
  } catch {
    return { available: false, turns: [] };
  }

  const users = new Map();
  const assistants = new Map();
  let pendingUser = "";
  const order = [];

  const remember = (turnId) => {
    const id = String(turnId);
    if (!order.includes(id)) order.push(id);
    return id;
  };

  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    let obj;
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }
    if (obj.type === "context.append_message" && obj.message?.role === "user") {
      pendingUser = textFromContent(obj.message.content) || pendingUser;
      continue;
    }
    if (obj.type === "turn.prompt") {
      // Same user turn is also recorded as append_message; keep prompt only as fallback.
      if (!pendingUser) pendingUser = textFromContent(obj.input);
      continue;
    }
    if (obj.type !== "context.append_loop_event") continue;
    const event = obj.event || {};
    if (event.type === "content.part" && event.part?.type === "text") {
      const turnId = remember(event.turnId ?? order.length);
      if (pendingUser) {
        users.set(turnId, (users.get(turnId) || "") + (users.has(turnId) ? "\n" : "") + pendingUser);
        pendingUser = "";
      }
      const chunk = event.part.text || "";
      if (chunk) {
        assistants.set(
          turnId,
          (assistants.get(turnId) || "") + (assistants.has(turnId) ? "" : "") + chunk,
        );
      }
    }
  }

  const turns = [];
  let skipping = lastTurnId != null && lastTurnId !== "";
  for (const turnId of order) {
    if (skipping) {
      if (String(turnId) === String(lastTurnId)) skipping = false;
      continue;
    }
    const user = cleanKimicodeText(users.get(turnId) || "");
    const assistant = cleanKimicodeText(assistants.get(turnId) || "");
    if (user) turns.push({ role: "user", content: user, turnId: String(turnId) });
    if (assistant) turns.push({ role: "assistant", content: assistant, turnId: String(turnId) });
  }
  return { available: true, turns };
}

/**
 * @param {object} input Hook stdin payload.
 * @param {object} state Persisted hook state (lastTurnId, pendingPrompt).
 * @returns {Array<{role: string, content: string, turnId?: string}>}
 */
export function buildKimicodeTurns(input = {}, state = {}) {
  const wirePath = resolveWirePath(input);
  const wire = extractUnseenWireTurns(wirePath, state.lastTurnId || null);
  if (wire.available) return wire.turns;

  const assistantContent =
    input.responseText ||
    input.responsePreview ||
    input.last_assistant_message ||
    input.assistant_message ||
    "";
  const userContent =
    input.prompt ||
    input.user_prompt ||
    input.user_message ||
    input.message ||
    state.pendingPrompt?.prompt ||
    "";
  return [
    { role: "user", content: cleanKimicodeText(userContent) },
    { role: "assistant", content: cleanKimicodeText(assistantContent) },
  ].filter((turn) => turn.content);
}
