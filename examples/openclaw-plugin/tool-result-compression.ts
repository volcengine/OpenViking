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
 * All thresholds are configured via `MemoryOpenVikingConfig` in config.ts and
 * passed through as-is by the context engine.
 */

import { mkdir, writeFile } from "node:fs/promises";
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

const TOOL_RESULTS_SUBDIR = "tool-results";
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

/**
 * Extract plain text from a tool result message (string or content-block array).
 */
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
  const bytes = chars;
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Stable filename derived from toolCallId (or a hash of content as fallback).
 */
function makePersistFilename(msg: AgentMessage): string {
  if (msg.toolCallId) return `${msg.toolCallId.replace(/[^a-zA-Z0-9_-]/g, "_")}.txt`;
  const text = getToolResultText(msg);
  const hash = createHash("sha256").update(text).digest("hex").slice(0, 16);
  return `result-${hash}.txt`;
}

/**
 * Resolve the base directory for persisted tool results.
 * Defaults to ~/.openclaw/memory/tool-results.
 */
function resolveStorageDir(override?: string): string {
  if (override) return override;
  return join(homedir(), ".openclaw", "memory", TOOL_RESULTS_SUBDIR);
}

async function ensureDir(dir: string): Promise<void> {
  try {
    await mkdir(dir, { recursive: true });
  } catch {
    // may already exist
  }
}

/**
 * Persist full tool result text to disk. Returns the file path, or null on
 * failure.  Uses `wx` flag to avoid overwriting if the same file was already
 * persisted in a prior turn.
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
  } catch (err: unknown) {
    const code = (err as NodeJS.ErrnoException)?.code;
    if (code !== "EEXIST") return null;
  }
  return filepath;
}

/**
 * Build the preview message that replaces the original content, including
 * the file path so the model knows where to find the full output.
 */
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

/**
 * Returns true when the tail of the text contains signals that are valuable
 * for the model — error messages, stack traces, closing braces (JSON), or
 * result/summary keywords.  When this is the case we keep both a head and a
 * tail section in the preview.
 */
function hasImportantTail(text: string): boolean {
  const tail = text.slice(-2000).toLowerCase();
  return (
    /\b(error|exception|failed|fatal|traceback|panic|stack trace|errno|exit code)\b/.test(tail) ||
    /\}\s*$/.test(text.trim()) ||
    /\b(total|summary|result|complete|finished|done)\b/.test(tail)
  );
}

/**
 * Generate a preview string from the full text, respecting line boundaries.
 * When the tail contains important signals, returns both head and tail.
 */
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
 * Truncate text for aggregate budget enforcement (no disk persistence,
 * just in-memory truncation with a simple marker).
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
 * Apply two-level tool-result compression to an assembled message array.
 *
 * 1. Persist any individual result exceeding `toolResultMaxChars` to disk and
 *    replace it with a preview + file path reference.
 * 2. If the aggregate of all results still exceeds
 *    `toolResultAggregateBudgetChars`, progressively truncate the largest
 *    results (greedy, descending by size) until the budget is satisfied.
 *
 * Returns a new array (shallow-copied) plus compression statistics.
 */
export async function compressToolResults(
  messages: AgentMessage[],
  cfg: {
    toolResultCompression: boolean;
    toolResultMaxChars: number;
    toolResultAggregateBudgetChars: number;
    toolResultPreviewChars: number;
    toolResultStorageDir?: string;
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
  const storageDir = resolveStorageDir(cfg.toolResultStorageDir);

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
        // Fallback: in-memory truncation if disk write failed.
        const { preview, hasMore } = generatePreview(text, previewChars);
        newContent = preview + (hasMore ? "\n\n[... disk persistence failed, content truncated ...]" : "");
      }

      const content = entry.message.content;
      if (typeof content === "string" || !Array.isArray(content)) {
        return { index: entry.index, message: { ...entry.message, content: newContent } };
      }

      const newContentBlocks = content.map((block: unknown) => {
        const b = block as Record<string, unknown>;
        if (b?.type === "text") return { ...b, text: newContent };
        return block;
      });
      return { index: entry.index, message: { ...entry.message, content: newContentBlocks } };
    });

    const persisted = await Promise.all(persistOps);
    for (const p of persisted) {
      result[p.index] = p.message;
      compressedCount++;
    }
  }

  // Phase 2: aggregate budget enforcement (in-memory truncation).
  const afterIndividual = collectToolResults(result);
  let aggregateChars = 0;
  for (const entry of afterIndividual) {
    aggregateChars += entry.textLength;
  }

  if (aggregateChars > aggregateBudget) {
    aggregateBudgetTriggered = true;
    const candidates = afterIndividual
      .filter(e => e.textLength > previewChars * 2)
      .sort((a, b) => b.textLength - a.textLength);

    let remaining = aggregateChars - aggregateBudget;
    for (const entry of candidates) {
      if (remaining <= 0) break;
      const targetChars = Math.max(previewChars, entry.textLength - remaining);

      const msg = result[entry.index]!;
      const text = getToolResultText(msg);
      const truncated = truncateText(text, targetChars);

      const content = msg.content;
      if (typeof content === "string" || !Array.isArray(content)) {
        result[entry.index] = { ...msg, content: truncated };
      } else {
        const newBlocks = content.map((block: unknown) => {
          const b = block as Record<string, unknown>;
          if (b?.type === "text") return { ...b, text: truncated };
          return block;
        });
        result[entry.index] = { ...msg, content: newBlocks };
      }

      const newTextLen = getToolResultTextLength(result[entry.index]!);
      remaining -= Math.max(0, entry.textLength - newTextLen);
      if (newTextLen < entry.textLength) compressedCount++;
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
