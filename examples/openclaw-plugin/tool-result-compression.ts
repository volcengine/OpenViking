/**
 * Tool result compression for the openclaw-plugin context engine.
 *
 * Prevents oversized tool outputs from bloating the assembled context that gets
 * sent back to the model.  When a tool result exceeds `toolResultMaxChars` the
 * full content is persisted to a file on disk and replaced with a preview +
 * file path reference, so the model can re-read the complete output later if
 * needed.
 *
 * Two levels of protection:
 *
 *   1. **Individual persistence** — any single tool result exceeding
 *      `toolResultMaxChars` is written to disk and replaced with a preview
 *      snippet that includes the file path.
 *
 *   2. **Aggregate budget** — if the *total* size of all tool results in one
 *      assembled context still exceeds `toolResultAggregateBudgetChars` after
 *      individual persistence, the largest results are progressively
 *      re-truncated until the budget is met.
 *
 * File persistence is scoped per-session to avoid cross-session collisions.
 * For array-type tool results (multiple text blocks), the budget is distributed
 * proportionally across blocks, preserving the original block structure.
 *
 * All thresholds are configured via `MemoryOpenVikingConfig` in config.ts and
 * passed through as-is by the context engine.
 */

import { mkdir, writeFile, readFile } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";
import { createHash } from "node:crypto";

type AgentMessage = {
  role?: string;
  content?: unknown;
  toolCallId?: string;
  toolName?: string;
  isError?: boolean;
};

const TOOL_RESULTS_BASE_DIR = "tool-results";
const PREVIEW_TAG_OPEN = "<persisted-output>";
const PREVIEW_TAG_CLOSE = "</persisted-output>";

export type ToolResultCompressionStats = {
  compressedCount: number;
  totalOriginalChars: number;
  totalCompressedChars: number;
  aggregateBudgetTriggered: boolean;
  persistedFiles: string[];
};

function isToolResultMessage(msg: AgentMessage): boolean {
  return msg.role === "toolResult";
}

function getToolResultTextLength(msg: AgentMessage): number {
  const content = msg.content;
  if (typeof content === "string") return content.length;
  if (Array.isArray(content)) {
    let total = 0;
    for (const block of content) {
      const b = block as Record<string, unknown>;
      if (b?.type === "text" && typeof b.text === "string") {
        total += b.text.length;
      }
    }
    return total;
  }
  return 0;
}

function getToolResultText(msg: AgentMessage): string {
  const content = msg.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    const parts: string[] = [];
    for (const block of content) {
      const b = block as Record<string, unknown>;
      if (b?.type === "text" && typeof b.text === "string") {
        parts.push(b.text);
      }
    }
    return parts.join("\n");
  }
  return "";
}

function formatFileSize(chars: number): string {
  if (chars < 1024) return `${chars} B`;
  if (chars < 1024 * 1024) return `${(chars / 1024).toFixed(1)} KB`;
  return `${(chars / (1024 * 1024)).toFixed(1)} MB`;
}

function contentHash(text: string): string {
  return createHash("sha256").update(text).digest("hex").slice(0, 16);
}

/**
 * Filename derived from toolCallId + content hash.  The hash suffix ensures
 * that different content for the same toolCallId (e.g. after a retry) produces
 * a distinct file instead of silently reusing a stale one.
 */
function makePersistFilename(msg: AgentMessage): string {
  const text = getToolResultText(msg);
  const hash = contentHash(text);
  if (msg.toolCallId) {
    const safeId = msg.toolCallId.replace(/[^a-zA-Z0-9_-]/g, "_");
    return `${safeId}-${hash}.txt`;
  }
  return `result-${hash}.txt`;
}

/**
 * Resolve session-scoped storage directory:
 *   override / sessionId /
 *   ~/.openclaw/memory/tool-results/<sessionId>/
 */
function resolveStorageDir(sessionId: string, override?: string): string {
  const base = override ?? join(homedir(), ".openclaw", "memory", TOOL_RESULTS_BASE_DIR);
  const safeSession = sessionId.replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 64);
  return join(base, safeSession);
}

async function ensureDir(dir: string): Promise<void> {
  try {
    await mkdir(dir, { recursive: true });
  } catch {
    // may already exist
  }
}

/**
 * Persist full tool result text to disk. Returns the file path on success,
 * null on failure.
 *
 * On EEXIST (file already written by a prior assemble of the same session),
 * we verify the content matches by reading it back.  If the content differs
 * (stale file from a different run), we append a short collision suffix and
 * retry once.
 */
async function persistToDisk(
  text: string,
  filename: string,
  storageDir: string,
): Promise<string | null> {
  await ensureDir(storageDir);
  const filepath = join(storageDir, filename);
  try {
    await writeFile(filepath, text, { encoding: "utf-8", flag: "wx" });
    return filepath;
  } catch (err: unknown) {
    const code = (err as NodeJS.ErrnoException)?.code;
    if (code !== "EEXIST") return null;
  }

  // File exists — verify content matches (same session, same toolCallId + content hash).
  try {
    const existing = await readFile(filepath, { encoding: "utf-8" });
    if (existing === text) return filepath;
  } catch {
    // unreadable — fall through to collision retry
  }

  // Content mismatch: append collision counter and retry.
  const dotIdx = filename.lastIndexOf(".");
  const base = dotIdx > 0 ? filename.slice(0, dotIdx) : filename;
  const ext = dotIdx > 0 ? filename.slice(dotIdx) : ".txt";
  const collisionName = `${base}_v2${ext}`;
  const collisionPath = join(storageDir, collisionName);
  try {
    await writeFile(collisionPath, text, { encoding: "utf-8", flag: "wx" });
    return collisionPath;
  } catch {
    return null;
  }
}

function buildPersistedPreview(
  originalSize: number,
  previewText: string,
  hasMore: boolean,
  filepath: string,
  previewChars: number,
): string {
  let msg = `${PREVIEW_TAG_OPEN}\n`;
  msg += `Output too large (${formatFileSize(originalSize)}). Full output saved to: ${filepath}\n\n`;
  msg += `Preview (first ${formatFileSize(previewChars)}):\n`;
  msg += previewText;
  if (hasMore) msg += "\n...\n";
  msg += PREVIEW_TAG_CLOSE;
  return msg;
}

function hasImportantTail(text: string): boolean {
  const tail = text.slice(-2000).toLowerCase();
  return (
    /\b(error|exception|failed|fatal|traceback|panic|stack trace|errno|exit code)\b/.test(tail) ||
    /\}\s*$/.test(text.trim()) ||
    /\b(total|summary|result|complete|finished|done)\b/.test(tail)
  );
}

function generatePreview(
  text: string,
  previewChars: number,
): { preview: string; hasMore: boolean } {
  if (text.length <= previewChars) {
    return { preview: text, hasMore: false };
  }

  if (hasImportantTail(text)) {
    const tailBudget = Math.min(Math.floor(previewChars * 0.3), 2000);
    const headBudget = previewChars - tailBudget;

    let headCut = headBudget;
    const headNewline = text.lastIndexOf("\n", headBudget);
    if (headNewline > headBudget * 0.8) headCut = headNewline;

    let tailStart = text.length - tailBudget;
    const tailNewline = text.indexOf("\n", tailStart);
    if (tailNewline !== -1 && tailNewline < tailStart + tailBudget * 0.2) {
      tailStart = tailNewline + 1;
    }

    const preview = text.slice(0, headCut) + "\n\n[... content omitted ...]\n\n" + text.slice(tailStart);
    return { preview, hasMore: true };
  }

  let cutPoint = previewChars;
  const lastNewline = text.lastIndexOf("\n", cutPoint);
  if (lastNewline > cutPoint * 0.8) cutPoint = lastNewline;

  return { preview: text.slice(0, cutPoint), hasMore: true };
}

/**
 * Truncate a single text string to fit within maxChars, respecting line
 * boundaries.  Used for aggregate budget enforcement.
 */
function truncateText(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  let cutPoint = maxChars;
  const lastNewline = text.lastIndexOf("\n", cutPoint);
  if (lastNewline > cutPoint * 0.8) cutPoint = lastNewline;
  return text.slice(0, cutPoint) + `\n\n[... truncated: showing ${formatFileSize(cutPoint)} of ${formatFileSize(text.length)} ...]`;
}

type ToolResultEntry = {
  index: number;
  message: AgentMessage;
  textLength: number;
};

function collectToolResults(messages: AgentMessage[]): ToolResultEntry[] {
  const entries: ToolResultEntry[] = [];
  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if (isToolResultMessage(msg)) {
      entries.push({ index: i, message: msg, textLength: getToolResultTextLength(msg) });
    }
  }
  return entries;
}

/**
 * Group toolResult entries by assistant turn.  A "group" is a maximal run of
 * non-assistant messages between two assistant messages — i.e. all toolResult
 * messages that belong to the same parallel tool-call batch.
 *
 * This matches Claude Code's `collectCandidatesByMessage` which groups
 * consecutive user messages not separated by an assistant message.
 */
function groupByAssistantTurn(messages: AgentMessage[], entries: ToolResultEntry[]): ToolResultEntry[][] {
  const groups: ToolResultEntry[][] = [];
  let current: ToolResultEntry[] = [];

  const flush = () => {
    if (current.length > 0) {
      groups.push(current);
      current = [];
    }
  };

  const entryByIndex = new Map(entries.map(e => [e.index, e]));

  for (let i = 0; i < messages.length; i++) {
    const msg = messages[i];
    if ((msg as AgentMessage).role === "assistant") {
      flush();
      continue;
    }
    const entry = entryByIndex.get(i);
    if (entry) {
      current.push(entry);
    }
  }
  flush();

  return groups;
}

/**
 * Replace the text content of a tool-result message with `newText`.
 * For array-type content, distributes the text proportionally across text
 * blocks (each block gets its own truncated slice), preserving non-text blocks.
 * For string-type content, replaces directly.
 */
function replaceToolResultText(
  msg: AgentMessage,
  newText: string,
): AgentMessage {
  const content = msg.content;
  if (typeof content === "string" || !Array.isArray(content)) {
    return { ...msg, content: newText };
  }

  // Array content: distribute proportionally per block (like OpenClaw's
  // truncateToolResultMessage).
  const totalTextLen = getToolResultTextLength(msg);
  if (totalTextLen === 0) return msg;

  let assigned = 0;
  const textBlocks = content.filter(
    (b: unknown) => (b as Record<string, unknown>)?.type === "text" && typeof (b as Record<string, unknown>).text === "string",
  );

  const newContent = content.map((block: unknown) => {
    const b = block as Record<string, unknown>;
    if (b?.type !== "text" || typeof b.text !== "string") return block;

    const blockLen = b.text.length;
    const blockShare = blockLen / totalTextLen;
    const blockBudget = Math.max(1, Math.floor(newText.length * blockShare));

    const start = Math.min(assigned, newText.length);
    const end = Math.min(start + blockBudget, newText.length);
    assigned = end;
    return { ...b, text: newText.slice(start, end) };
  });

  return { ...msg, content: newContent };
}

/**
 * Truncate a tool-result message's array content blocks proportionally,
 * each block truncated independently.  Mirrors OpenClaw's
 * truncateToolResultMessage approach.
 */
function truncateToolResultMessage(
  msg: AgentMessage,
  maxChars: number,
): AgentMessage {
  const content = msg.content;
  if (typeof content === "string") {
    return { ...msg, content: truncateText(content, maxChars) };
  }
  if (!Array.isArray(content)) return msg;

  const totalTextLen = getToolResultTextLength(msg);
  if (totalTextLen <= maxChars) return msg;

  const newContent = content.map((block: unknown) => {
    const b = block as Record<string, unknown>;
    if (b?.type !== "text" || typeof b.text !== "string") return block;

    const blockShare = b.text.length / totalTextLen;
    const blockBudget = Math.max(200, Math.floor(maxChars * blockShare));
    return { ...b, text: truncateText(b.text, blockBudget) };
  });

  return { ...msg, content: newContent };
}

export async function compressToolResults(
  messages: AgentMessage[],
  cfg: {
    toolResultCompression: boolean;
    toolResultMaxChars: number;
    toolResultAggregateBudgetChars: number;
    toolResultPreviewChars: number;
    toolResultStorageDir?: string;
    sessionId?: string;
  },
): Promise<{ messages: AgentMessage[]; stats: ToolResultCompressionStats }> {
  if (!cfg.toolResultCompression) {
    return {
      messages,
      stats: { compressedCount: 0, totalOriginalChars: 0, totalCompressedChars: 0, aggregateBudgetTriggered: false, persistedFiles: [] },
    };
  }

  const maxChars = cfg.toolResultMaxChars;
  const previewChars = cfg.toolResultPreviewChars;
  const aggregateBudget = cfg.toolResultAggregateBudgetChars;
  const sessionId = cfg.sessionId ?? "default";
  const storageDir = resolveStorageDir(sessionId, cfg.toolResultStorageDir);

  const entries = collectToolResults(messages);
  if (entries.length === 0) {
    return {
      messages,
      stats: { compressedCount: 0, totalOriginalChars: 0, totalCompressedChars: 0, aggregateBudgetTriggered: false, persistedFiles: [] },
    };
  }

  let totalOriginalChars = 0;
  for (const entry of entries) {
    totalOriginalChars += entry.textLength;
  }

  const result = [...messages];
  let compressedCount = 0;
  let totalCompressedChars = 0;
  let aggregateBudgetTriggered = false;
  const persistedFiles: string[] = [];

  // Phase 1: persist oversized results to disk, replace with preview + path.
  const oversized = entries.filter(e => e.textLength > maxChars);
  if (oversized.length > 0) {
    const persistOps = oversized.map(async (entry) => {
      const text = getToolResultText(entry.message);
      const filename = makePersistFilename(entry.message);
      const filepath = await persistToDisk(text, filename, storageDir);

      let newContent: string;
      if (filepath) {
        const { preview, hasMore } = generatePreview(text, previewChars);
        newContent = buildPersistedPreview(text.length, preview, hasMore, filepath, previewChars);
        persistedFiles.push(filepath);
      } else {
        const { preview, hasMore } = generatePreview(text, previewChars);
        newContent = preview + (hasMore ? "\n\n[... disk persistence failed, content truncated ...]" : "");
      }

      return {
        index: entry.index,
        message: replaceToolResultText(entry.message, newContent),
      };
    });

    const persisted = await Promise.all(persistOps);
    for (const p of persisted) {
      result[p.index] = p.message;
      compressedCount++;
    }
  }

  // Phase 2: aggregate budget enforcement per assistant turn.
  // Each group = toolResult messages between two assistant messages.
  const afterIndividual = collectToolResults(result);
  const groups = groupByAssistantTurn(result, afterIndividual);

  for (const group of groups) {
    let groupChars = 0;
    for (const entry of group) {
      groupChars += entry.textLength;
    }

    if (groupChars > aggregateBudget) {
      aggregateBudgetTriggered = true;
      const candidates = [...group]
        .filter(e => e.textLength > previewChars * 2)
        .sort((a, b) => b.textLength - a.textLength);

      let remaining = groupChars - aggregateBudget;
      for (const entry of candidates) {
        if (remaining <= 0) break;
        const targetChars = Math.max(previewChars, entry.textLength - remaining);
        result[entry.index] = truncateToolResultMessage(result[entry.index]!, targetChars);

        const newTextLen = getToolResultTextLength(result[entry.index]!);
        remaining -= Math.max(0, entry.textLength - newTextLen);
        if (newTextLen < entry.textLength) compressedCount++;
      }
    }
  }

  const finalEntries = collectToolResults(result);
  for (const entry of finalEntries) {
    totalCompressedChars += entry.textLength;
  }

  return {
    messages: result,
    stats: { compressedCount, totalOriginalChars, totalCompressedChars, aggregateBudgetTriggered, persistedFiles },
  };
}
