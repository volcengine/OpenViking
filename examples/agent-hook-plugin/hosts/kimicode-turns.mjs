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

function textFromContent(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
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

  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    let row;
    try {
      row = JSON.parse(line);
    } catch {
      continue;
    }
    if (row.type === "context.append_message" && row.message?.role === "user") {
      pendingUser = textFromContent(row.message.content) || pendingUser;
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
    const event = row.event || {};
    if (event.type === "turn.ended") {
      attachPending(event.turnId ?? row.turnId);
      continue;
    }
    if (event.type === "content.part" && event.part?.type === "text") {
      const turnId = attachPending(event.turnId);
      assistants.set(turnId, `${assistants.get(turnId) || ""}${event.part.text || ""}`);
    }
  }

  const turns = [];
  const lastIndex = lastTurnId == null || lastTurnId === ""
    ? -1
    : order.indexOf(String(lastTurnId));
  for (const turnId of order.slice(lastIndex + 1)) {
    const user = cleanKimicodeText(users.get(turnId) || "");
    const assistant = cleanKimicodeText(assistants.get(turnId) || "");
    if (user) turns.push({ role: "user", content: user, turnId });
    if (assistant) turns.push({ role: "assistant", content: assistant, turnId });
  }
  return { status: "ok", turns };
}

export function buildKimicodeTurns(input = {}, state = {}) {
  return extractUnseenKimicodeTurns(
    resolveKimicodeWirePath(input),
    state.lastTurnId || null,
  );
}
