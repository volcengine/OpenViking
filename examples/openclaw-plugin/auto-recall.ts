import type { FindResult, FindResultItem, OpenVikingClient } from "./client.js";
import type { ParsedMemoryOpenVikingConfig } from "./config.js";
import {
  pickMemoriesForInjection,
  postProcessMemories,
  summarizeInjectionMemories,
  toJsonLog,
} from "./memory-ranking.js";
import { quickRecallPrecheck, withTimeout } from "./process-manager.js";
import { sanitizeUserTextForCapture } from "./text-utils.js";
import { estimateTextTokens } from "./token-estimator.js";
import type {
  RecallResourceType,
  RecallTraceEntry,
  RecallTraceResult,
} from "./recall-trace.js";

const RECALL_QUERY_MAX_CHARS = 4_000;
export const AUTO_RECALL_SOURCE_MARKER = "Source: openviking-auto-recall";
export const OPENVIKING_CONTEXT_TAG = "openviking-context";

type Logger = {
  info: (msg: string) => void;
  warn?: (msg: string) => void;
};

const WRITE_OR_EFFECT_RE =
  /\b(write|edit|modify|delete|remove|migrate|deploy|release|publish|configure|patch)\b|写|改|修改|删除|迁移|部署|发布|配置|打补丁/i;
const EXECUTION_RE =
  /\b(fix|debug|test|build|run|implement|refactor|integrate|repair|troubleshoot)\b|修复|调试|测试|构建|运行|实现|重构|对接|排查/i;
const FAILURE_RE =
  /\b(error|exception|traceback|failed|failure|retry|exit code|test failed)\b|报错|异常|失败|重试|挂了|不通过/i;
const ENGINEERING_OBJECT_RE =
  /(?:^|\s)(?:[\w.-]+\/[\w./-]+|[\w./-]+\.(?:ts|tsx|js|jsx|py|go|rs|java|md|json|ya?ml|toml|sh|sql))\b|`[^`]+`|\b(?:repo|workspace|plugin|service|component|hook|api|tool|package|module)\b|仓库|工作区|插件|服务|组件|接口|工具|模块|文件/i;
const EXPERIENCE_INTENT_RE =
  /经验|踩坑|最佳实践|不要再|按之前|avoid|best practice|lesson|pitfall/i;
const QUESTION_ONLY_RE =
  /^(?:什么是|是什么|区别|解释|讲讲|怎么看|为什么|如何理解|where is|what is|explain|difference between)\b|[?？]$/i;
const CASUAL_RE = /闲聊|翻译|总结当前对话|天气|笑话|hello|hi\b|你好/i;

export type ExperienceRecallTrigger =
  | "task_start"
  | "skill_load"
  | "subagent_start"
  | "write_preflight"
  | "cron_start";

export type ExperienceRecallDecision = {
  recall: boolean;
  trigger?: ExperienceRecallTrigger;
  score: number;
  reason: string;
};

export type PreparedRecallQuery = {
  query: string;
  truncated: boolean;
  originalChars: number;
  finalChars: number;
};

export function prepareRecallQuery(rawText: string): PreparedRecallQuery {
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

  const query =
    sanitized.length > RECALL_QUERY_MAX_CHARS
      ? sanitized.slice(0, RECALL_QUERY_MAX_CHARS).trim()
      : sanitized;

  return {
    query,
    truncated: sanitized.length > RECALL_QUERY_MAX_CHARS,
    originalChars,
    finalChars: query.length,
  };
}

/** Estimate token count using the shared CJK-aware fallback for diagnostics. */
export function estimateTokenCount(text: string): number {
  return estimateTextTokens(text);
}

export type BuildMemoryLinesOptions = {
  recallPreferAbstract: boolean;
  includeUri?: boolean;
};

function memoryCategory(item: FindResultItem): string {
  return item.category?.trim() || "memory";
}

function indentContent(content: string): string {
  return content
    .split("\n")
    .map((line) => `  ${line}`)
    .join("\n");
}

function formatMemoryLine(
  item: FindResultItem,
  content: string,
  options: BuildMemoryLinesOptions,
): string {
  const category = memoryCategory(item);
  if (!options.includeUri) {
    return `- [${category}] ${content}`;
  }

  return [
    `- [${category}]`,
    `  <uri>${item.uri}</uri>`,
    indentContent(content),
  ].join("\n");
}

function isExperienceMemory(item: FindResultItem): boolean {
  const category = (item.category ?? "").toLowerCase();
  return (
    item.uri.includes("/memories/experiences/") ||
    item.uri.includes("/experiences/") ||
    category === "experience" ||
    category === "experiences"
  );
}

async function resolveMemoryContent(
  item: FindResultItem,
  readFn: (uri: string) => Promise<string>,
  options: BuildMemoryLinesOptions,
): Promise<string> {
  let content: string;

  if (options.recallPreferAbstract && item.abstract?.trim()) {
    content = item.abstract.trim();
  } else if (item.level === 2) {
    try {
      const fullContent = await readFn(item.uri);
      content =
        fullContent && typeof fullContent === "string" && fullContent.trim()
          ? fullContent.trim()
          : (item.abstract?.trim() || item.uri);
    } catch {
      content = item.abstract?.trim() || item.uri;
    }
  } else {
    content = item.abstract?.trim() || item.uri;
  }

  return content;
}

export async function buildMemoryLines(
  memories: FindResultItem[],
  readFn: (uri: string) => Promise<string>,
  options: BuildMemoryLinesOptions,
): Promise<string[]> {
  const lines: string[] = [];
  for (const item of memories) {
    const content = await resolveMemoryContent(item, readFn, options);
    lines.push(formatMemoryLine(item, content, options));
  }
  return lines;
}

export type BuildMemoryLinesWithBudgetOptions = BuildMemoryLinesOptions & {
  recallMaxInjectedChars?: number;
  recallTokenBudget?: number;
};

/**
 * Build memory lines with a character budget constraint.
 *
 * Individual memories are never truncated. A memory that cannot fit within the
 * remaining character budget is skipped so only complete memory entries are
 * injected.
 */
export async function buildMemoryLinesWithBudget(
  memories: FindResultItem[],
  readFn: (uri: string) => Promise<string>,
  options: BuildMemoryLinesWithBudgetOptions,
): Promise<{ lines: string[]; estimatedTokens: number }> {
  const charBudget = options.recallMaxInjectedChars ?? options.recallTokenBudget ?? 0;
  const lines: string[] = [];
  let totalTokens = 0;
  let totalChars = 0;

  for (const item of memories) {
    if (totalChars >= charBudget) {
      break;
    }

    const content = await resolveMemoryContent(item, readFn, options);
    const line = formatMemoryLine(item, content, options);
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

export function buildLongTermMemorySection(memoryLines: string[]): string {
  return [
    "## Long-term Memories",
    "",
    AUTO_RECALL_SOURCE_MARKER,
    "The following OpenViking memories may be relevant:",
    ...memoryLines,
  ].join("\n");
}

export function buildOpenVikingContextBlock(params: {
  sections: Array<string | undefined>;
}): string {
  const sections = params.sections
    .map((section) => section?.trim())
    .filter((section): section is string => Boolean(section));
  if (sections.length === 0) {
    return "";
  }
  return [
    `<${OPENVIKING_CONTEXT_TAG}>`,
    sections.join("\n\n"),
    `</${OPENVIKING_CONTEXT_TAG}>`,
  ].join("\n");
}

function newTraceId(): string {
  return `auto_recall_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}

function preview(value: string | undefined | null, maxChars: number): string | undefined {
  const normalized = typeof value === "string" ? value.trim().replace(/\s+/g, " ") : "";
  if (!normalized) {
    return undefined;
  }
  return normalized.length > maxChars ? normalized.slice(0, maxChars) : normalized;
}

function traceResourceTypeForUri(uri: string | undefined): RecallResourceType {
  return uri?.startsWith("viking://resources") ? "resource" : "user";
}

function toTraceResults(items: FindResultItem[], resourceType: RecallResourceType): RecallTraceResult[] {
  return items.map((item) => ({
    uri: item.uri,
    resourceType,
    category: item.category,
    score: item.score,
    level: item.level,
    abstractPreview: preview(item.abstract ?? item.overview, 240),
    resultType: resourceType === "resource" ? "resource" : "memory",
  }));
}

function boundTraceQuery(query: string, maxChars: number): { query: string; queryTruncated?: boolean } {
  return query.length <= maxChars
    ? { query }
    : { query: query.slice(0, maxChars), queryTruncated: true };
}

function runtimeFlag(runtimeContext: unknown, key: string): unknown {
  return runtimeContext && typeof runtimeContext === "object"
    ? (runtimeContext as Record<string, unknown>)[key]
    : undefined;
}

export function isCronSession(sessionKey?: string, runtimeContext?: unknown): boolean {
  return Boolean(
    sessionKey?.includes(":cron:") ||
      runtimeFlag(runtimeContext, "isCron") === true ||
      runtimeFlag(runtimeContext, "automationKind") === "cron",
  );
}

export function shouldRecallAgentExperience(input: {
  latestUserText: string;
  sessionKey?: string;
  runtimeContext?: unknown;
  triggerHint?: ExperienceRecallTrigger;
  minQueryChars?: number;
  isBypassed?: boolean;
}): ExperienceRecallDecision {
  const text = sanitizeUserTextForCapture(input.latestUserText).trim();
  const minQueryChars = input.minQueryChars ?? 12;

  if (input.isBypassed) {
    return { recall: false, score: 0, reason: "session_bypassed" };
  }
  if (!text || text.length < minQueryChars) {
    return { recall: false, score: 0, reason: "query_too_short" };
  }
  if (/<openviking-context\b/i.test(input.latestUserText)) {
    return { recall: false, score: 0, reason: "already_injected" };
  }

  const trigger = input.triggerHint ?? (isCronSession(input.sessionKey, input.runtimeContext) ? "cron_start" : "task_start");
  if (trigger !== "task_start") {
    return { recall: true, trigger, score: 99, reason: "forced_trigger" };
  }

  let score = 0;
  if (WRITE_OR_EFFECT_RE.test(text)) score += 3;
  if (EXECUTION_RE.test(text)) score += 2;
  if (FAILURE_RE.test(text)) score += 2;
  if (ENGINEERING_OBJECT_RE.test(text)) score += 2;
  if (EXPERIENCE_INTENT_RE.test(text)) score += 1;

  if (CASUAL_RE.test(text)) score -= 3;
  if (QUESTION_ONLY_RE.test(text) && !ENGINEERING_OBJECT_RE.test(text) && !EXECUTION_RE.test(text)) {
    score -= 2;
  }

  if (score >= 3) {
    return { recall: true, trigger: "task_start", score, reason: "task_execution" };
  }
  return { recall: false, score, reason: score < 0 ? "non_execution" : "below_threshold" };
}

function sectionAfter(markdown: string, heading: string): string {
  const re = new RegExp(`(?:^|\\n)##\\s+${heading}\\s*\\n([\\s\\S]*?)(?=\\n##\\s+|$)`, "i");
  return markdown.match(re)?.[1]?.trim() ?? "";
}

function bulletize(text: string, fallback: string): string[] {
  const cleaned = text
    .split(/\n+/)
    .map((line) => line.replace(/^[-*]\s*/, "").trim())
    .filter(Boolean);
  const lines = cleaned.length > 0 ? cleaned : [fallback];
  return lines.slice(0, 5).map((line) => `- ${line}`);
}

function titleFromUri(uri: string): string {
  const raw = decodeURIComponent(uri.split("/").pop() ?? "experience").replace(/\.md$/i, "");
  return raw || "experience";
}

function renderExperience(item: FindResultItem, content: string): string | null {
  const situation = sectionAfter(content, "Situation");
  const approach = sectionAfter(content, "Approach");
  const reflect = sectionAfter(content, "Reflect");
  const hasStructuredExperience = Boolean(situation || approach || reflect);
  if (!hasStructuredExperience && !isExperienceMemory(item)) {
    return null;
  }

  const score = typeof item.score === "number" ? item.score.toFixed(3) : "n/a";
  return [
    `### Experience: ${titleFromUri(item.uri)}`,
    `Source: ${item.uri}`,
    `Score: ${score}`,
    "",
    "Trigger:",
    ...bulletize(situation, item.abstract?.trim() || content.slice(0, 240).trim() || "Similar execution situation."),
    "",
    "Do:",
    ...bulletize(approach, "Use the proven execution path from this experience."),
    "",
    "Avoid:",
    ...bulletize(reflect, "Avoid repeating failure modes called out by this experience."),
    "",
    "Scope:",
    ...bulletize(situation, "Applies to similar agent execution tasks."),
    "",
    "Check:",
    ...bulletize(reflect || approach, "Verify the task outcome before final response."),
  ].join("\n");
}

export async function buildAgentExperienceRecallContext(params: {
  cfg: ParsedMemoryOpenVikingConfig;
  client: OpenVikingClient;
  agentId: string;
  queryText: string;
  trigger: ExperienceRecallTrigger;
  logger: Logger;
  verbose?: (message: string) => void;
}): Promise<{ block?: string; count: number; estimatedTokens: number; skippedReason?: string }> {
  const { cfg, client, agentId, queryText, trigger, logger, verbose } = params;
  const expCfg = cfg.agentExperience;
  if (!expCfg.enabled) {
    return { count: 0, estimatedTokens: 0, skippedReason: "disabled" };
  }

  const precheck = await quickRecallPrecheck(client, agentId);
  if (!precheck.ok) {
    verbose?.(`openviking: skipping agent experience recall because precheck failed (${precheck.reason})`);
    return { count: 0, estimatedTokens: 0, skippedReason: precheck.reason };
  }

  return withTimeout(
    (async () => {
      const result = await client.find(queryText, {
        targetUri: "viking://user/memories/experiences",
        limit: Math.max(expCfg.recallLimit * 4, 12),
        scoreThreshold: expCfg.scoreThreshold,
      }, agentId);

      const candidates = (result.memories ?? [])
        .filter(isExperienceMemory)
        .filter((item, index, self) => index === self.findIndex((m) => m.uri === item.uri))
        .slice(0, expCfg.recallLimit);

      if (candidates.length === 0) {
        return { count: 0, estimatedTokens: 0, skippedReason: "no_hits" };
      }

      const rendered: string[] = [];
      let chars = 0;
      for (const item of candidates) {
        const content = item.level === 2
          ? await client.read(item.uri, agentId).catch(() => item.abstract ?? item.uri)
          : item.abstract ?? item.overview ?? item.uri;
        const exp = renderExperience(item, content);
        if (!exp) continue;
        const projected = chars + (rendered.length > 0 ? 2 : 0) + exp.length;
        if (projected > expCfg.maxInjectedChars) continue;
        rendered.push(exp);
        chars = projected;
      }

      if (rendered.length === 0) {
        return { count: 0, estimatedTokens: 0, skippedReason: "no_structured_hits" };
      }

      const block = [
        "## Agent Experiences",
        "",
        "These are prior execution lessons learned by this agent. Use them as task guidance, not as user facts.",
        "",
        ...rendered,
      ].join("\n");
      verbose?.(`openviking: injecting ${rendered.length} agent experiences for trigger=${trigger}`);
      return { block, count: rendered.length, estimatedTokens: estimateTokenCount(block) };
    })(),
    cfg.autoRecallTimeoutMs,
    "openviking: agent experience recall timeout",
  ).catch((err) => {
    logger.warn?.(`openviking: agent experience recall failed: ${String(err)}`);
    return { count: 0, estimatedTokens: 0, skippedReason: "failed" };
  });
}

export async function buildGatedAgentExperienceRecallContext(params: {
  cfg: ParsedMemoryOpenVikingConfig;
  client: OpenVikingClient;
  agentId: string;
  queryText: string;
  sessionKey?: string;
  runtimeContext?: unknown;
  logger: Logger;
  verbose?: (message: string) => void;
}): Promise<{ block?: string; count: number; estimatedTokens: number; skippedReason?: string }> {
  const { cfg, client, agentId, queryText, sessionKey, runtimeContext, logger, verbose } = params;
  if (!cfg.agentExperience.enabled) {
    return { count: 0, estimatedTokens: 0, skippedReason: "disabled" };
  }

  const triggerHint = isCronSession(sessionKey, runtimeContext)
    ? "cron_start"
    : "task_start";
  const decision = shouldRecallAgentExperience({
    latestUserText: queryText,
    sessionKey,
    runtimeContext,
    triggerHint,
    minQueryChars: cfg.agentExperience.minQueryChars,
    isBypassed: false,
  });
  if (!decision.recall) {
    return { count: 0, estimatedTokens: 0, skippedReason: decision.reason };
  }

  return buildAgentExperienceRecallContext({
    cfg,
    client,
    agentId,
    queryText,
    trigger: decision.trigger ?? "task_start",
    logger,
    verbose,
  });
}

export async function buildLongTermMemoryRecallContext(params: {
  cfg: ParsedMemoryOpenVikingConfig;
  client: OpenVikingClient;
  agentId: string;
  peerId?: string;
  queryText: string;
  logger: Logger;
  verbose?: (message: string) => void;
  traceRecorder?: { record(entry: RecallTraceEntry): void };
  sessionId?: string;
  sessionKey?: string;
  ovSessionId?: string;
  rawUserTextPreview?: string;
  queryTruncated?: boolean;
}): Promise<{ section?: string; memoryCount: number; estimatedTokens: number }> {
  const { cfg, client, agentId, peerId, queryText, logger, verbose } = params;

  if (!cfg.autoRecall || queryText.length < 5) {
    return { memoryCount: 0, estimatedTokens: 0 };
  }

  const precheck = await quickRecallPrecheck(client, agentId);
  if (!precheck.ok) {
    verbose?.(`openviking: skipping auto-recall because precheck failed (${precheck.reason})`);
    return { memoryCount: 0, estimatedTokens: 0 };
  }

  return withTimeout(
    (async () => {
      const candidateLimit = Math.max(cfg.recallLimit * 4, 20);
      const targetUris = [
        "viking://user/memories",
        ...(cfg.recallResources ? ["viking://resources"] : []),
      ];
      const autoRecallPromises = targetUris.map(async (targetUri) => {
        const started = Date.now();
        const result = await client.find(queryText, {
          targetUri,
          limit: candidateLimit,
          scoreThreshold: 0,
          peerId,
        }, agentId);
        return {
          targetUri,
          result,
          durationMs: Date.now() - started,
        };
      });
      const traceSearches: RecallTraceEntry["searches"] = [];
      const autoRecallSettled = await Promise.allSettled(autoRecallPromises);

      const allMemories: FindResultItem[] = [];
      for (let index = 0; index < autoRecallSettled.length; index += 1) {
        const s = autoRecallSettled[index]!;
        const targetUri = targetUris[index]!;
        const resourceType = traceResourceTypeForUri(targetUri);
        if (s.status === "fulfilled") {
          const result = s.value.result;
          const memories = result.memories ?? [];
          const resources = result.resources ?? [];
          allMemories.push(...memories, ...resources);
          traceSearches.push({
            resourceType,
            targetUriInput: targetUri,
            targetUriResolved: targetUri,
            limit: candidateLimit,
            scoreThreshold: 0,
            durationMs: s.value.durationMs,
            total: result.total ?? memories.length + resources.length + (result.skills?.length ?? 0),
            results: [
              ...toTraceResults(memories, resourceType),
              ...toTraceResults(resources, "resource"),
              ...(result.skills ?? []).map((item): RecallTraceResult => ({
                uri: item.uri,
                resourceType,
                category: item.category,
                score: item.score,
                level: item.level,
                abstractPreview: preview(item.abstract ?? item.overview, 240),
                resultType: "skill",
              })),
            ].slice(0, cfg.traceRecallMaxResultsPerSearch),
          });
        } else {
          logger.warn?.(`openviking: auto-recall search failed: ${String(s.reason)}`);
          traceSearches.push({
            resourceType,
            targetUriInput: targetUri,
            targetUriResolved: targetUri,
            limit: candidateLimit,
            scoreThreshold: 0,
            durationMs: 0,
            total: 0,
            results: [],
            error: String(s.reason),
          });
        }
      }

      const uniqueMemories = allMemories.filter((memory, index, self) =>
        index === self.findIndex((m) => m.uri === memory.uri)
      );
      const leafOnly = uniqueMemories.filter((m) => (!m.level || m.level === 2) && !isExperienceMemory(m));
      const processed = postProcessMemories(leafOnly, {
        limit: candidateLimit,
        scoreThreshold: cfg.recallScoreThreshold,
      });
      const memories = pickMemoriesForInjection(processed, cfg.recallLimit, queryText);
      const resourceTypes = [...new Set(traceSearches
        .map((search) => search.resourceType)
        .filter((resourceType): resourceType is RecallResourceType => resourceType !== "archive"))];
      const recordTrace = (injectedMemories: FindResultItem[], injectedCount: number, estimatedTokens?: number) => {
        params.traceRecorder?.record({
          schemaVersion: "1.0",
          traceId: newTraceId(),
          ts: Date.now(),
          sessionId: params.sessionId,
          sessionKey: params.sessionKey,
          ovSessionId: params.ovSessionId,
          agentId,
          source: "auto_recall",
          operationType: "semantic_find",
          resourceTypes: resourceTypes.length > 0 ? resourceTypes : ["user"],
          trigger: {
            rawUserTextPreview: params.rawUserTextPreview,
            ...boundTraceQuery(queryText, cfg.traceRecallQueryMaxChars),
            queryTruncated: params.queryTruncated || queryText.length > cfg.traceRecallQueryMaxChars,
          },
          searches: traceSearches,
          selected: injectedMemories.map((memory) => ({
            uri: memory.uri,
            resourceType: traceResourceTypeForUri(memory.uri),
            category: memory.category,
            score: memory.score,
            abstractPreview: preview(memory.abstract ?? memory.overview, cfg.traceRecallPreviewChars),
            injected: true,
          })),
          stats: {
            candidateCount: allMemories.length,
            selectedCount: injectedMemories.length,
            injectedCount,
            estimatedTokens,
          },
        });
      };

      if (memories.length === 0) {
        recordTrace([], 0, 0);
        return { memoryCount: 0, estimatedTokens: 0 };
      }

      const { lines: memoryLines, estimatedTokens } = await buildMemoryLinesWithBudget(
        memories,
        (uri) => client.read(uri, agentId),
        {
          recallPreferAbstract: cfg.recallPreferAbstract,
          recallMaxInjectedChars: cfg.recallMaxInjectedChars,
          includeUri: true,
        },
      );

      if (memoryLines.length === 0) {
        verbose?.(
          `openviking: skipping auto-recall injection; no complete memories fit maxInjectedChars=${cfg.recallMaxInjectedChars}`,
        );
        recordTrace([], 0, 0);
        return { memoryCount: 0, estimatedTokens: 0 };
      }

      const section = buildLongTermMemorySection(memoryLines);
      verbose?.(
        `openviking: injecting ${memoryLines.length} memories (${section.length} chars, ~${estimatedTokens} tokens, maxInjectedChars=${cfg.recallMaxInjectedChars})`,
      );
      verbose?.(
        `openviking: inject-detail ${toJsonLog({ count: memories.length, memories: summarizeInjectionMemories(memories) })}`,
      );

      recordTrace(memories.slice(0, memoryLines.length), memoryLines.length, estimatedTokens);
      return { section, memoryCount: memoryLines.length, estimatedTokens };
    })(),
    cfg.autoRecallTimeoutMs,
    "openviking: auto-recall search timeout",
  );
}
