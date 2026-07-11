import type { RecallResourceType } from "../registries/recall-resource-types.js";

export function previewText(value: unknown, maxChars: number): string | undefined {
  const text = typeof value === "string" ? value.replace(/\s+/g, " ").trim() : "";
  if (!text) {
    return undefined;
  }
  return text.length <= maxChars ? text : `${text.slice(0, Math.max(0, maxChars - 1))}…`;
}

export function inferRecallResourceType(uri: string): RecallResourceType | undefined {
  if (uri.startsWith("viking://resources")) return "resource";
  if (uri.startsWith("viking://user/skills") || uri.startsWith("viking://skills")) return "agent";
  if (uri.startsWith("viking://user/")) return "user";
  return undefined;
}

export function createTraceId(source: string): string {
  return `${source}-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function createMemoryStoreTempSessionId(): string {
  return `memory-store-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function boundTraceQuery(query: string, maxChars: number): { query: string; queryTruncated?: boolean } {
  if (query.length <= maxChars) {
    return { query };
  }
  return { query: query.slice(0, maxChars), queryTruncated: true };
}

export function extractToolSenderId(ctx: unknown): string | undefined {
  if (!ctx || typeof ctx !== "object") {
    return undefined;
  }
  const toolCtx = ctx as Record<string, unknown>;
  if (typeof toolCtx.requesterSenderId === "string") {
    const trimmed = toolCtx.requesterSenderId.trim();
    if (trimmed) {
      return trimmed;
    }
  }
  if (typeof toolCtx.senderId === "string") {
    const trimmed = toolCtx.senderId.trim();
    if (trimmed) {
      return trimmed;
    }
  }
  return undefined;
}

export function makeBypassedToolResult(toolName: string) {
  return {
    content: [
      {
        type: "text" as const,
        text: `OpenViking is bypassed for this session by bypassSessionPatterns; ${toolName} was skipped.`,
      },
    ],
    details: {
      action: "bypassed",
      reason: "session_bypassed",
      toolName,
    },
  };
}
