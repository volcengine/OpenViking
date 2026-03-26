import { createHash } from "node:crypto";
import type { OpenVikingClient } from "./client.js";
import type { MemoryOpenVikingConfig } from "./config.js";
import {
  getCaptureDecision,
  extractNewTurnTexts,
} from "./text-utils.js";
import {
  trimForLog,
  toJsonLog,
} from "./memory-ranking.js";

type AgentMessage = {
  role?: string;
  content?: unknown;
  toolName?: string;
  toolCallId?: string;
  details?: unknown;
  isError?: boolean;
  [key: string]: unknown;
};

type ContextEngineInfo = {
  id: string;
  name: string;
  version?: string;
};

type AssembleResult = {
  messages: AgentMessage[];
  estimatedTokens: number;
  systemPromptAddition?: string;
};

type IngestResult = {
  ingested: boolean;
};

type IngestBatchResult = {
  ingestedCount: number;
};

type CompactResult = {
  ok: boolean;
  compacted: boolean;
  reason?: string;
  result?: unknown;
};

type ContextEngine = {
  info: ContextEngineInfo;
  ingest: (params: { sessionId: string; message: AgentMessage; isHeartbeat?: boolean }) => Promise<IngestResult>;
  ingestBatch?: (params: {
    sessionId: string;
    messages: AgentMessage[];
    isHeartbeat?: boolean;
  }) => Promise<IngestBatchResult>;
  afterTurn?: (params: {
    sessionId: string;
    sessionFile: string;
    messages: AgentMessage[];
    prePromptMessageCount: number;
    autoCompactionSummary?: string;
    isHeartbeat?: boolean;
    tokenBudget?: number;
    runtimeContext?: Record<string, unknown>;
  }) => Promise<void>;
  assemble: (params: { sessionId: string; messages: AgentMessage[]; tokenBudget?: number }) => Promise<AssembleResult>;
  compact: (params: {
    sessionId: string;
    sessionFile: string;
    tokenBudget?: number;
    force?: boolean;
    currentTokenCount?: number;
    compactionTarget?: "budget" | "threshold";
    customInstructions?: string;
    runtimeContext?: Record<string, unknown>;
  }) => Promise<CompactResult>;
};

export type ContextEngineWithSessionMapping = ContextEngine & {
  /** Return the OV session ID for an OpenClaw sessionKey using a stable cross-platform-safe mapping. */
  getOVSessionForKey: (sessionKey: string) => string;
  /** Ensure an OV session exists on the server for the given OpenClaw sessionKey (auto-created by getSession if absent). */
  resolveOVSession: (sessionKey: string) => Promise<string>;
  /** Commit (extract + archive) then delete the OV session, so a fresh one is created on next use. */
  commitOVSession: (sessionKey: string) => Promise<void>;
};

type Logger = {
  info: (msg: string) => void;
  warn?: (msg: string) => void;
  error: (msg: string) => void;
};

function md5Short(input: string): string {
  return createHash("md5").update(input).digest("hex").slice(0, 12);
}

const SAFE_SESSION_KEY_RE = /^[A-Za-z0-9_-]+$/;

export function mapSessionKeyToOVSessionId(sessionKey: string): string {
  const normalized = sessionKey.trim();
  if (!normalized) {
    return "openclaw_session";
  }
  if (SAFE_SESSION_KEY_RE.test(normalized)) {
    return normalized;
  }

  const readable = normalized
    .replace(/[^A-Za-z0-9_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 48);
  const digest = md5Short(normalized);
  return readable ? `openclaw_${readable}_${digest}` : `openclaw_session_${digest}`;
}

function estimateTokens(messages: AgentMessage[]): number {
  const chars = messages.reduce((sum, message) => sum + estimateMessageChars(message), 0);
  return Math.max(1, Math.ceil(chars / 4));
}

function estimateMessageChars(message: AgentMessage): number {
  if (!message || typeof message !== "object") {
    return 0;
  }
  let chars = 0;
  if (Array.isArray(message.content)) {
    for (const block of message.content) {
      if (!block || typeof block !== "object") {
        continue;
      }
      const text = (block as Record<string, unknown>).text;
      if (typeof text === "string") {
        chars += text.length;
      }
    }
  } else if (typeof message.content === "string") {
    chars += message.content.length;
  }
  if (message.details && typeof message.details === "object") {
    try {
      chars += JSON.stringify(message.details).length;
    } catch {
      // ignore
    }
  }
  return chars + 32;
}

const IMPORTANT_LINE_RE = /(error|warn|exception|traceback|failed|failure|fatal|denied|forbidden|unauthorized|not found|enoent|eacces|eperm|timeout|timed out|refused|unreachable|no such|404|401|403|429|500|curl:|stderr|exit code)/i;
const AGGRESSIVE_DETAIL_KEYS = new Set([
  "aggregated",
  "text",
  "html",
  "markdown",
  "content",
  "snapshot",
  "rawHtml",
  "dom",
  "console",
  "body",
]);
const TOOL_CONTEXT_COMPRESSIBLE = new Set(["exec", "process", "web_fetch", "browser"]);

function cloneMessage(message: AgentMessage): AgentMessage {
  return JSON.parse(JSON.stringify(message)) as AgentMessage;
}

function getTextBlocks(message: AgentMessage): Array<{ index: number; text: string }> {
  const content = message.content;
  if (!Array.isArray(content)) {
    return [];
  }
  const blocks: Array<{ index: number; text: string }> = [];
  for (let i = 0; i < content.length; i += 1) {
    const block = content[i];
    if (!block || typeof block !== "object") {
      continue;
    }
    const blockObj = block as Record<string, unknown>;
    if (blockObj.type === "text" && typeof blockObj.text === "string") {
      blocks.push({ index: i, text: blockObj.text });
    }
  }
  return blocks;
}

function setSingleTextBlock(message: AgentMessage, nextText: string): void {
  const content = Array.isArray(message.content) ? [...message.content] : [];
  let replaced = false;
  for (let i = 0; i < content.length; i += 1) {
    const block = content[i];
    if (!block || typeof block !== "object") {
      continue;
    }
    const blockObj = block as Record<string, unknown>;
    if (blockObj.type === "text") {
      content[i] = { ...blockObj, text: nextText };
      replaced = true;
      break;
    }
  }
  if (!replaced) {
    content.unshift({ type: "text", text: nextText });
  }
  message.content = content;
}

function trimChars(text: string, maxChars: number): string {
  if (text.length <= maxChars) {
    return text;
  }
  if (maxChars <= 3) {
    return text.slice(0, maxChars);
  }
  return `${text.slice(0, maxChars - 3)}...`;
}

function trimMiddle(text: string, headChars: number, tailChars: number): string {
  if (text.length <= headChars + tailChars + 5) {
    return text;
  }
  return `${text.slice(0, headChars)}\n...\n${text.slice(-tailChars)}`;
}

function dedupeLines(lines: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) {
      continue;
    }
    const key = line.toLowerCase();
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    out.push(line);
  }
  return out;
}

function stripExternalWrapper(text: string): string {
  let out = text.replace(/<<<EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>/g, "")
    .replace(/<<<END_EXTERNAL_UNTRUSTED_CONTENT[^>]*>>>/g, "")
    .replace(/\r\n/g, "\n");

  if (out.startsWith("SECURITY NOTICE:")) {
    const marker = out.indexOf("Source:");
    if (marker > 0) {
      out = out.slice(marker);
    }
  }

  return out.replace(/\n{3,}/g, "\n\n").trim();
}

function sanitizeInline(text: unknown, maxChars: number): string | undefined {
  if (typeof text !== "string") {
    return undefined;
  }
  const normalized = stripExternalWrapper(text).replace(/\s+/g, " ").trim();
  if (!normalized) {
    return undefined;
  }
  return trimChars(normalized, maxChars);
}

function safeParseJson(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{") || !trimmed.endsWith("}")) {
    return null;
  }
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
    return null;
  } catch {
    return null;
  }
}

function compactDetails(toolName: string, details: unknown, cfg: Required<MemoryOpenVikingConfig>): Record<string, unknown> | undefined {
  if (!details || typeof details !== "object" || Array.isArray(details)) {
    return details && typeof details === "object" && !Array.isArray(details)
      ? { ...(details as Record<string, unknown>) }
      : undefined;
  }

  const source = details as Record<string, unknown>;
  if (toolName === "exec" || toolName === "process") {
    const next: Record<string, unknown> = {};
    for (const key of ["status", "exitCode", "durationMs", "cwd", "sessionId", "retryInMs", "name"]) {
      if (typeof source[key] !== "undefined") {
        next[key] = key === "name" && typeof source[key] === "string"
          ? trimChars(source[key] as string, 120)
          : source[key];
      }
    }
    return next;
  }

  const next: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(source)) {
    if (AGGRESSIVE_DETAIL_KEYS.has(key)) {
      continue;
    }
    next[key] = typeof value === "string" ? trimChars(value, 240) : value;
  }
  return next;
}

function summarizeExecLikeText(text: string, toolName: string, details: Record<string, unknown> | undefined, cfg: Required<MemoryOpenVikingConfig>): string {
  const normalized = String(text ?? "").replace(/\r\n/g, "\n").trim();
  if (!normalized || normalized === "(no output)" || normalized === "(no new output)\n\nProcess still running.") {
    return normalized || text;
  }

  if (normalized.length <= cfg.compressToolContextAboveChars) {
    return normalized;
  }

  const lines = normalized.split("\n");
  const importantLines = dedupeLines(lines.filter((line) => IMPORTANT_LINE_RE.test(line))).slice(0, cfg.compressToolContextMaxImportantLines);
  const meta: string[] = [];
  if (typeof details?.status === "string") meta.push(`status=${details.status}`);
  if (typeof details?.exitCode === "number") meta.push(`exitCode=${details.exitCode}`);
  if (typeof details?.durationMs === "number") meta.push(`durationMs=${details.durationMs}`);
  if (typeof details?.retryInMs === "number") meta.push(`retryInMs=${details.retryInMs}`);

  const parts: string[] = [`[compressed ${toolName} result]`];
  if (meta.length > 0) {
    parts.push(meta.join(" "));
  }
  parts.push(`originalChars=${normalized.length} originalLines=${lines.length}`);

  if (importantLines.length > 0) {
    parts.push("important:");
    parts.push(...importantLines.map((line) => `- ${trimChars(line, Math.max(120, cfg.compressToolContextMaxChars - 40))}`));
  }

  const tail = trimChars(lines.slice(-Math.max(4, Math.min(12, cfg.compressToolContextMaxImportantLines))).join("\n"), cfg.compressToolContextTailChars);
  if (tail) {
    parts.push("tail:");
    parts.push(tail);
  }

  if (importantLines.length === 0) {
    parts.push("excerpt:");
    parts.push(trimMiddle(normalized, cfg.compressToolContextHeadChars, cfg.compressToolContextTailChars));
  }

  return trimChars(parts.join("\n"), cfg.compressToolContextMaxChars);
}

function summarizeStructuredFetch(toolName: string, text: string, details: Record<string, unknown> | undefined, cfg: Required<MemoryOpenVikingConfig>): { text: string; details?: Record<string, unknown> } | null {
  const parsed = safeParseJson(text);
  const source = parsed ?? details;
  if (!source || typeof source !== "object") {
    return null;
  }

  const textExcerpt = sanitizeInline(
    (source as Record<string, unknown>).text
      ?? (source as Record<string, unknown>).markdown
      ?? (source as Record<string, unknown>).content,
    Math.max(200, Math.min(cfg.compressToolContextMaxChars - 400, 1200)),
  );

  const compact: Record<string, unknown> = {
    tool: toolName,
    url: typeof source.url === "string" ? source.url : undefined,
    finalUrl: typeof source.finalUrl === "string" && source.finalUrl !== source.url ? source.finalUrl : undefined,
    status: typeof source.status === "number" || typeof source.status === "string" ? source.status : undefined,
    title: sanitizeInline(source.title, 180),
    contentType: typeof source.contentType === "string" ? source.contentType : undefined,
    extractMode: typeof source.extractMode === "string" ? source.extractMode : undefined,
    truncated: typeof source.truncated === "boolean" ? source.truncated : undefined,
    length: typeof source.length === "number" ? source.length : undefined,
    tookMs: typeof source.tookMs === "number" ? source.tookMs : undefined,
    untrustedExternal: typeof source.externalContent === "object" && source.externalContent !== null
      ? Boolean((source.externalContent as Record<string, unknown>).untrusted)
      : undefined,
    excerpt: textExcerpt,
  };

  const cleaned = Object.fromEntries(Object.entries(compact).filter(([, value]) => typeof value !== "undefined"));
  const nextText = JSON.stringify(cleaned, null, 2);
  return {
    text: trimChars(nextText, cfg.compressToolContextMaxChars),
    details: cleaned,
  };
}

function compressToolResultMessage(message: AgentMessage, cfg: Required<MemoryOpenVikingConfig>): { message: AgentMessage; changed: boolean; savedChars: number } {
  if (message.role !== "toolResult" || !cfg.compressToolContext) {
    return { message, changed: false, savedChars: 0 };
  }

  const toolName = typeof message.toolName === "string" ? message.toolName : "";
  const enabled = TOOL_CONTEXT_COMPRESSIBLE.has(toolName) || (toolName === "read" && cfg.compressReadToolContext);
  if (!enabled) {
    return { message, changed: false, savedChars: 0 };
  }

  const next = cloneMessage(message);
  const textBlocks = getTextBlocks(next);
  const firstText = textBlocks[0]?.text;
  const details = next.details && typeof next.details === "object" && !Array.isArray(next.details)
    ? { ...(next.details as Record<string, unknown>) }
    : undefined;

  if (!firstText) {
    return { message, changed: false, savedChars: 0 };
  }

  if (firstText.length <= cfg.compressToolContextAboveChars) {
    return { message, changed: false, savedChars: 0 };
  }

  let nextText = firstText;
  let nextDetails = details;

  if (toolName === "web_fetch" || toolName === "browser") {
    const summarized = summarizeStructuredFetch(toolName, firstText, details, cfg);
    if (summarized) {
      nextText = summarized.text;
      nextDetails = summarized.details;
    } else {
      nextText = trimChars(trimMiddle(firstText, cfg.compressToolContextHeadChars, cfg.compressToolContextTailChars), cfg.compressToolContextMaxChars);
      nextDetails = compactDetails(toolName, details, cfg);
    }
  } else {
    nextText = summarizeExecLikeText(firstText, toolName, details, cfg);
    nextDetails = compactDetails(toolName, details, cfg);
  }

  setSingleTextBlock(next, nextText);
  if (nextDetails) {
    next.details = nextDetails;
  } else if (typeof next.details !== "undefined") {
    delete next.details;
  }

  const savedChars = Math.max(0, firstText.length - nextText.length);
  return { message: next, changed: nextText !== firstText, savedChars };
}

function compressMessagesForContext(messages: AgentMessage[], cfg: Required<MemoryOpenVikingConfig>, logger: Logger): AgentMessage[] {
  if (!cfg.compressToolContext) {
    return messages;
  }

  let compressedCount = 0;
  let savedChars = 0;
  const nextMessages = messages.map((message) => {
    const compressed = compressToolResultMessage(message, cfg);
    if (compressed.changed) {
      compressedCount += 1;
      savedChars += compressed.savedChars;
      return compressed.message;
    }
    return message;
  });

  if (compressedCount > 0) {
    logger.info(`openviking: assemble compressed ${compressedCount} tool results, saved ~${savedChars} chars`);
  }

  return nextMessages;
}

async function tryLegacyCompact(params: {
  sessionId: string;
  sessionFile: string;
  tokenBudget?: number;
  force?: boolean;
  currentTokenCount?: number;
  compactionTarget?: "budget" | "threshold";
  customInstructions?: string;
  runtimeContext?: Record<string, unknown>;
}): Promise<CompactResult | null> {
  const candidates = [
    "openclaw/context-engine/legacy",
    "openclaw/dist/context-engine/legacy.js",
  ];

  for (const path of candidates) {
    try {
      const mod = (await import(path)) as {
        LegacyContextEngine?: new () => {
          compact: (arg: typeof params) => Promise<CompactResult>;
        };
      };
      if (!mod?.LegacyContextEngine) {
        continue;
      }
      const legacy = new mod.LegacyContextEngine();
      return legacy.compact(params);
    } catch {
      // continue
    }
  }

  return null;
}

function warnOrInfo(logger: Logger, message: string): void {
  if (typeof logger.warn === "function") {
    logger.warn(message);
    return;
  }
  logger.info(message);
}

export function createMemoryOpenVikingContextEngine(params: {
  id: string;
  name: string;
  version?: string;
  cfg: Required<MemoryOpenVikingConfig>;
  logger: Logger;
  getClient: () => Promise<OpenVikingClient>;
  resolveAgentId: (sessionId: string) => string;
}): ContextEngineWithSessionMapping {
  const {
    id,
    name,
    version,
    cfg,
    logger,
    getClient,
    resolveAgentId,
  } = params;

  async function doCommitOVSession(sessionKey: string): Promise<void> {
    try {
      const client = await getClient();
      const agentId = resolveAgentId(sessionKey);
      const ovSessionId = mapSessionKeyToOVSessionId(sessionKey);
      const commitResult = await client.commitSession(ovSessionId, { wait: true, agentId });
      logger.info(
        `openviking: committed OV session for sessionKey=${sessionKey}, ovSessionId=${ovSessionId}, archived=${commitResult.archived ?? false}, memories=${commitResult.memories_extracted ?? 0}, task_id=${commitResult.task_id ?? "none"}`,
      );
      await client.deleteSession(ovSessionId, agentId).catch(() => {});
    } catch (err) {
      warnOrInfo(logger, `openviking: commit failed for sessionKey=${sessionKey}: ${String(err)}`);
    }
  }

  function extractSessionKey(runtimeContext: Record<string, unknown> | undefined): string | undefined {
    if (!runtimeContext) {
      return undefined;
    }
    const key = runtimeContext.sessionKey;
    return typeof key === "string" && key.trim() ? key.trim() : undefined;
  }

  return {
    info: {
      id,
      name,
      version,
    },

    // --- session-mapping extensions ---

    getOVSessionForKey: (sessionKey: string) => mapSessionKeyToOVSessionId(sessionKey),

    async resolveOVSession(sessionKey: string): Promise<string> {
      return mapSessionKeyToOVSessionId(sessionKey);
    },

    commitOVSession: doCommitOVSession,

    // --- standard ContextEngine methods ---

    async ingest(): Promise<IngestResult> {
      return { ingested: false };
    },

    async ingestBatch(): Promise<IngestBatchResult> {
      return { ingestedCount: 0 };
    },

    async assemble(assembleParams): Promise<AssembleResult> {
      const messages = compressMessagesForContext(assembleParams.messages, cfg, logger);
      return {
        messages,
        estimatedTokens: estimateTokens(messages),
      };
    },

    async afterTurn(afterTurnParams): Promise<void> {
      if (!cfg.autoCapture) {
        return;
      }

      try {
        const sessionKey = extractSessionKey(afterTurnParams.runtimeContext);
        const agentId = resolveAgentId(sessionKey ?? afterTurnParams.sessionId);

        const messages = afterTurnParams.messages ?? [];
        if (messages.length === 0) {
          logger.info("openviking: auto-capture skipped (messages=0)");
          return;
        }

        const start =
          typeof afterTurnParams.prePromptMessageCount === "number" &&
          afterTurnParams.prePromptMessageCount >= 0
            ? afterTurnParams.prePromptMessageCount
            : 0;

        const { texts: newTexts, newCount } = extractNewTurnTexts(messages, start);

        if (newTexts.length === 0) {
          logger.info("openviking: auto-capture skipped (no new user/assistant messages)");
          return;
        }

        const turnText = newTexts.join("\n");
        const decision = getCaptureDecision(turnText, cfg.captureMode, cfg.captureMaxLength);
        const preview = turnText.length > 80 ? `${turnText.slice(0, 80)}...` : turnText;
        logger.info(
          "openviking: capture-check " +
            `shouldCapture=${String(decision.shouldCapture)} ` +
            `reason=${decision.reason} newMsgCount=${newCount} text=\"${preview}\"`,
        );

        if (!decision.shouldCapture) {
          logger.info("openviking: auto-capture skipped (capture decision rejected)");
          return;
        }

        const client = await getClient();
        const OVSessionId = sessionKey
          ? mapSessionKeyToOVSessionId(sessionKey)
          : afterTurnParams.sessionId;
        await client.addSessionMessage(OVSessionId, "user", decision.normalizedText, agentId);
        const commitResult = await client.commitSession(OVSessionId, { wait: true, agentId });
        logger.info(
          `openviking: committed ${newCount} messages in session=${OVSessionId}, ` +
            `archived=${commitResult.archived ?? false}, memories=${commitResult.memories_extracted ?? 0}, ` +
            `task_id=${commitResult.task_id ?? "none"} ${toJsonLog({ captured: [trimForLog(turnText, 260)] })}`,
        );
      } catch (err) {
        warnOrInfo(logger, `openviking: auto-capture failed: ${String(err)}`);
      }
    },

    async compact(compactParams): Promise<CompactResult> {
      const delegated = await tryLegacyCompact(compactParams);
      if (delegated) {
        return delegated;
      }

      warnOrInfo(
        logger,
        "openviking: legacy compaction delegation unavailable; skipping compact",
      );

      return {
        ok: true,
        compacted: false,
        reason: "legacy_compact_unavailable",
      };
    },
  };
}
