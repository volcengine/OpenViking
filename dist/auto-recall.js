import { pickMemoriesForInjection, postProcessMemories, summarizeInjectionMemories, toJsonLog, } from "./memory-ranking.js";
import { quickRecallPrecheck, withTimeout } from "./process-manager.js";
import { sanitizeUserTextForCapture } from "./text-utils.js";
const AUTO_RECALL_TIMEOUT_MS = 5_000;
const RECALL_QUERY_MAX_CHARS = 4_000;
export const AUTO_RECALL_SOURCE_MARKER = "Source: openviking-auto-recall";
export function prepareRecallQuery(rawText) {
    const sanitized = sanitizeUserTextForCapture(rawText).trim();
    const originalChars = sanitized.length;
    if (!sanitized) {
        return {
            query: "",
            truncated: false,
            originalChars: 0,
            finalChars: 0,
        };
    }
    const query = sanitized.length > RECALL_QUERY_MAX_CHARS
        ? sanitized.slice(0, RECALL_QUERY_MAX_CHARS).trim()
        : sanitized;
    return {
        query,
        truncated: sanitized.length > RECALL_QUERY_MAX_CHARS,
        originalChars,
        finalChars: query.length,
    };
}
/** Estimate token count using chars/4 heuristic for diagnostics. */
export function estimateTokenCount(text) {
    if (!text)
        return 0;
    return Math.ceil(text.length / 4);
}
async function resolveMemoryContent(item, readFn, options) {
    let content;
    if (options.recallPreferAbstract && item.abstract?.trim()) {
        content = item.abstract.trim();
    }
    else if (item.level === 2) {
        try {
            const fullContent = await readFn(item.uri);
            content =
                fullContent && typeof fullContent === "string" && fullContent.trim()
                    ? fullContent.trim()
                    : (item.abstract?.trim() || item.uri);
        }
        catch {
            content = item.abstract?.trim() || item.uri;
        }
    }
    else {
        content = item.abstract?.trim() || item.uri;
    }
    return content;
}
export async function buildMemoryLines(memories, readFn, options) {
    const lines = [];
    for (const item of memories) {
        const content = await resolveMemoryContent(item, readFn, options);
        lines.push(`- [${item.category ?? "memory"}] ${content}`);
    }
    return lines;
}
/**
 * Build memory lines with a character budget constraint.
 *
 * Individual memories are never truncated. A memory that cannot fit within the
 * remaining character budget is skipped so only complete memory entries are
 * injected.
 */
export async function buildMemoryLinesWithBudget(memories, readFn, options) {
    const charBudget = options.recallMaxInjectedChars ?? options.recallTokenBudget ?? 0;
    const lines = [];
    let totalTokens = 0;
    let totalChars = 0;
    for (const item of memories) {
        if (totalChars >= charBudget) {
            break;
        }
        const content = await resolveMemoryContent(item, readFn, options);
        const line = `- [${item.category ?? "memory"}] ${content}`;
        const separatorChars = lines.length > 0 ? 1 : 0;
        const projectedChars = totalChars + separatorChars + line.length;
        if (projectedChars > charBudget) {
            continue;
        }
        const lineTokens = estimateTokenCount(line);
        lines.push(line);
        totalTokens += lineTokens;
        totalChars = projectedChars;
    }
    return { lines, estimatedTokens: totalTokens };
}
export function buildRecallContextBlock(memoryLines) {
    return [
        "<relevant-memories>",
        AUTO_RECALL_SOURCE_MARKER,
        "The following OpenViking memories may be relevant:",
        ...memoryLines,
        "</relevant-memories>",
    ].join("\n");
}
export async function buildAutoRecallContext(params) {
    const { cfg, client, agentId, queryText, logger, verbose } = params;
    if (!cfg.autoRecall || queryText.length < 5) {
        return { memoryCount: 0, estimatedTokens: 0 };
    }
    const precheck = await quickRecallPrecheck(cfg.baseUrl);
    if (!precheck.ok) {
        verbose?.(`openviking: skipping auto-recall because precheck failed (${precheck.reason})`);
        return { memoryCount: 0, estimatedTokens: 0 };
    }
    return withTimeout((async () => {
        const candidateLimit = Math.max(cfg.recallLimit * 4, 20);
        const autoRecallPromises = [
            client.find(queryText, {
                targetUri: "viking://user/memories",
                limit: candidateLimit,
                scoreThreshold: 0,
            }, agentId),
            client.find(queryText, {
                targetUri: "viking://agent/memories",
                limit: candidateLimit,
                scoreThreshold: 0,
            }, agentId),
        ];
        if (cfg.recallResources) {
            autoRecallPromises.push(client.find(queryText, {
                targetUri: "viking://resources",
                limit: candidateLimit,
                scoreThreshold: 0,
            }, agentId));
        }
        const autoRecallSettled = await Promise.allSettled(autoRecallPromises);
        const allMemories = [];
        for (const s of autoRecallSettled) {
            if (s.status === "fulfilled") {
                allMemories.push(...(s.value.memories ?? []), ...(s.value.resources ?? []));
            }
            else {
                logger.warn?.(`openviking: auto-recall search failed: ${String(s.reason)}`);
            }
        }
        const uniqueMemories = allMemories.filter((memory, index, self) => index === self.findIndex((m) => m.uri === memory.uri));
        const leafOnly = uniqueMemories.filter((m) => !m.level || m.level === 2);
        const processed = postProcessMemories(leafOnly, {
            limit: candidateLimit,
            scoreThreshold: cfg.recallScoreThreshold,
        });
        const memories = pickMemoriesForInjection(processed, cfg.recallLimit, queryText);
        if (memories.length === 0) {
            return { memoryCount: 0, estimatedTokens: 0 };
        }
        const { lines: memoryLines, estimatedTokens } = await buildMemoryLinesWithBudget(memories, (uri) => client.read(uri, agentId), {
            recallPreferAbstract: cfg.recallPreferAbstract,
            recallMaxInjectedChars: cfg.recallMaxInjectedChars,
        });
        if (memoryLines.length === 0) {
            verbose?.(`openviking: skipping auto-recall injection; no complete memories fit maxInjectedChars=${cfg.recallMaxInjectedChars}`);
            return { memoryCount: 0, estimatedTokens: 0 };
        }
        const block = buildRecallContextBlock(memoryLines);
        verbose?.(`openviking: injecting ${memoryLines.length} memories (${block.length} chars, ~${estimatedTokens} tokens, maxInjectedChars=${cfg.recallMaxInjectedChars})`);
        verbose?.(`openviking: inject-detail ${toJsonLog({ count: memories.length, memories: summarizeInjectionMemories(memories) })}`);
        return { block, memoryCount: memoryLines.length, estimatedTokens };
    })(), AUTO_RECALL_TIMEOUT_MS, "openviking: auto-recall search timeout");
}
