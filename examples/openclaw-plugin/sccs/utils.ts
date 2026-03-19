import { createHash } from "node:crypto";
import { homedir } from "node:os";
import { resolve } from "node:path";

type ContentBlock = { type?: unknown; text?: unknown };
type MessageLike = { role?: unknown; content?: unknown };
const REF_ID_RE = /\[REF_ID: ([a-f0-9]{32})\]/i;

export function md5Hex(input: string): string {
  return createHash("md5").update(input).digest("hex");
}

export function hasRefId(text: string): boolean {
  return REF_ID_RE.test(text);
}

export function normalizeRefId(value: string): string {
  const match = value.match(REF_ID_RE);
  return match ? match[1] : value.trim();
}

export function resolveHomePath(pathValue: string): string {
  if (!pathValue) {
    return pathValue;
  }
  return pathValue.startsWith("~/")
    ? resolve(homedir(), pathValue.slice(2))
    : resolve(pathValue);
}

export function extractTextContent(content: unknown): string {
  if (typeof content === "string") {
    return content;
  }
  if (Array.isArray(content)) {
    return content
      .map((block: ContentBlock) =>
        block && typeof block === "object" && typeof block.text === "string" ? block.text : "",
      )
      .filter(Boolean)
      .join("\n");
  }
  if (content && typeof content === "object") {
    try {
      return JSON.stringify(content);
    } catch {
      return String(content);
    }
  }
  return "";
}

export function setTextContent(message: MessageLike, text: string): MessageLike {
  return { ...message, content: [{ type: "text", text }] };
}

export function isToolRole(role: unknown): boolean {
  return role === "tool" || role === "toolResult" || role === "tool_result";
}

export function estimateTokens(text: string): number {
  return Math.ceil(text.length / 4);
}

export function estimateTokensForMessages(messages: MessageLike[]): number {
  return messages.reduce((sum, msg) => sum + estimateTokens(extractTextContent(msg.content)), 0);
}
