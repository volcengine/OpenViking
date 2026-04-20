/**
 * Tool result compression for the openclaw-plugin context engine.
 *
 * Prevents oversized tool outputs from bloating the assembled context that gets
 * sent back to the model.  Two levels of protection:
 *
 *   1. **Individual truncation** — any single tool result exceeding
 *      `toolResultMaxChars` is truncated to a head (and optionally tail)
 *      preview.
 *
 *   2. **Aggregate budget** — if the *total* size of all tool results in one
 *      assembled context exceeds `toolResultAggregateBudgetChars`, the largest
 *      results are progressively re-truncated until the budget is met.
 *
 * Truncation prefers line boundaries and preserves the tail when it contains
 * error / stack-trace signals so the model can still diagnose failures.
 *
 * All thresholds are configured via `MemoryOpenVikingConfig` in config.ts and
 * passed through as-is by the context engine.
 */

type AgentMessage = {
  role?: string;
  content?: unknown;
  toolCallId?: string;
  toolName?: string;
  isError?: boolean;
};

const TRUNCATION_MARKER = "\n\n[... tool output truncated: showing first";
const TRUNCATION_TAIL_MARKER = " characters ...]";

export type ToolResultCompressionStats = {
  compressedCount: number;
  totalOriginalChars: number;
  totalCompressedChars: number;
  aggregateBudgetTriggered: boolean;
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
 * Returns true when the tail of the text contains signals that are valuable
 * for the model — error messages, stack traces, closing braces (JSON), or
 * result/summary keywords.  When this is the case we keep both a head and a
 * tail section instead of just the head.
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
 * Truncate a single text string to at most `maxChars`, keeping a preview of
 * `previewChars`.  When the tail looks important (errors / JSON closing) we
 * split the budget into head + tail so both ends are preserved.
 */
function truncateToolResultText(
  text: string,
  maxChars: number,
  previewChars: number,
): string {
  if (text.length <= maxChars) return text;

  const suffix = `${TRUNCATION_MARKER} ${previewChars} of ${text.length}${TRUNCATION_TAIL_MARKER}`;

  // Head + tail mode: preserve error messages / JSON closers at the end.
  if (hasImportantTail(text) && maxChars > previewChars * 2) {
    const tailBudget = Math.min(Math.floor(maxChars * 0.3), 4000);
    const headBudget = maxChars - tailBudget - suffix.length;

    if (headBudget > previewChars) {
      // Snap to line boundaries to avoid cutting mid-word.
      let headCut = headBudget;
      const headNewline = text.lastIndexOf("\n", headBudget);
      if (headNewline > headBudget * 0.8) headCut = headNewline;

      let tailStart = text.length - tailBudget;
      const tailNewline = text.indexOf("\n", tailStart);
      if (tailNewline !== -1 && tailNewline < tailStart + tailBudget * 0.2) {
        tailStart = tailNewline + 1;
      }

      return text.slice(0, headCut) + suffix + "\n\n[... tail content ...]\n\n" + text.slice(tailStart);
    }
  }

  // Head-only mode.
  let cutPoint = Math.max(previewChars, maxChars - suffix.length);
  const lastNewline = text.lastIndexOf("\n", cutPoint);
  if (lastNewline > cutPoint * 0.8) cutPoint = lastNewline;

  return text.slice(0, cutPoint) + suffix;
}

/**
 * Compress a single tool-result message in place.  Handles both plain-string
 * and content-block-array payloads.  Array payloads share the char budget
 * proportionally across text blocks.
 */
function compressSingleToolResult(
  msg: AgentMessage,
  maxChars: number,
  previewChars: number,
): AgentMessage {
  if (!isToolResultMessage(msg)) return msg;
  const textLength = getToolResultTextLength(msg);
  if (textLength <= maxChars) return msg;

  const content = msg.content;
  if (typeof content === "string") {
    return { ...msg, content: truncateToolResultText(content, maxChars, previewChars) };
  }

  if (Array.isArray(content)) {
    const totalTextLen = getToolResultTextLength(msg);
    const newContent = content.map((block: unknown) => {
      const b = block as Record<string, unknown>;
      if (b?.type === "text" && typeof b.text === "string") {
        const blockShare = b.text.length / totalTextLen;
        const blockBudget = Math.max(previewChars, Math.floor(maxChars * blockShare));
        return { ...b, text: truncateToolResultText(b.text, blockBudget, Math.floor(previewChars * blockShare)) };
      }
      return block;
    });
    return { ...msg, content: newContent };
  }

  return msg;
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
 * 1. Truncate any individual result exceeding `toolResultMaxChars`.
 * 2. If the aggregate of all results still exceeds
 *    `toolResultAggregateBudgetChars`, progressively re-truncate the largest
 *    results (greedy, descending by size) until the budget is satisfied.
 *
 * Returns a new array (shallow-copied) plus compression statistics.
 */
export function compressToolResults(
  messages: AgentMessage[],
  cfg: {
    toolResultCompression: boolean;
    toolResultMaxChars: number;
    toolResultAggregateBudgetChars: number;
    toolResultPreviewChars: number;
  },
): { messages: AgentMessage[]; stats: ToolResultCompressionStats } {
  if (!cfg.toolResultCompression) {
    return {
      messages,
      stats: { compressedCount: 0, totalOriginalChars: 0, totalCompressedChars: 0, aggregateBudgetTriggered: false },
    };
  }

  const maxChars = cfg.toolResultMaxChars;
  const previewChars = cfg.toolResultPreviewChars;
  const aggregateBudget = cfg.toolResultAggregateBudgetChars;

  const entries = collectToolResults(messages);
  if (entries.length === 0) {
    return {
      messages,
      stats: { compressedCount: 0, totalOriginalChars: 0, totalCompressedChars: 0, aggregateBudgetTriggered: false },
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

  // Phase 1: individual truncation.
  for (const entry of entries) {
    if (entry.textLength > maxChars) {
      result[entry.index] = compressSingleToolResult(entry.message, maxChars, previewChars);
      compressedCount++;
    }
  }

  // Phase 2: aggregate budget enforcement.
  const afterIndividual = collectToolResults(result);
  let aggregateChars = 0;
  for (const entry of afterIndividual) {
    aggregateChars += entry.textLength;
  }

  if (aggregateChars > aggregateBudget) {
    aggregateBudgetTriggered = true;
    const oversized = afterIndividual
      .filter(e => e.textLength > previewChars * 2)
      .sort((a, b) => b.textLength - a.textLength);

    let remaining = aggregateChars - aggregateBudget;
    for (const entry of oversized) {
      if (remaining <= 0) break;
      const targetChars = Math.max(previewChars, entry.textLength - remaining);
      result[entry.index] = compressSingleToolResult(result[entry.index]!, targetChars, previewChars);
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
    stats: { compressedCount, totalOriginalChars, totalCompressedChars, aggregateBudgetTriggered },
  };
}
