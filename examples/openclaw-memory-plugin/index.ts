import { spawn, execSync } from "node:child_process";
import { createHash } from "node:crypto";
import { readFileSync, existsSync } from "node:fs";
import { join } from "node:path";
import { homedir, tmpdir, platform } from "node:os";

const IS_WIN = platform() === "win32";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { Type } from "@sinclair/typebox";
import { memoryOpenVikingConfigSchema } from "./config.js";

type FindResultItem = {
  uri: string;
  is_leaf?: boolean;
  abstract?: string;
  overview?: string;
  category?: string;
  score?: number;
  match_reason?: string;
};

type FindResult = {
  memories?: FindResultItem[];
  resources?: FindResultItem[];
  skills?: FindResultItem[];
  total?: number;
};

type CaptureMode = "semantic" | "keyword";
type ScopeName = "user" | "agent";
type RuntimeIdentity = {
  userId: string;
  agentId: string;
};
type LocalClientCacheEntry = {
  client: OpenVikingClient;
  process: ReturnType<typeof spawn> | null;
};

const localClientCache = new Map<string, LocalClientCacheEntry>();

const MEMORY_URI_PATTERNS = [
  /^viking:\/\/user\/(?:[^/]+\/)?memories(?:\/|$)/,
  /^viking:\/\/agent\/(?:[^/]+\/)?memories(?:\/|$)/,
];
const USER_STRUCTURE_DIRS = new Set(["memories"]);
const AGENT_STRUCTURE_DIRS = new Set(["memories", "skills", "instructions", "workspaces"]);

function md5Short(input: string): string {
  return createHash("md5").update(input).digest("hex").slice(0, 12);
}

const MEMORY_TRIGGERS = [
  /remember|preference|prefer|important|decision|decided|always|never/i,
  /记住|偏好|喜欢|喜爱|崇拜|讨厌|害怕|重要|决定|总是|永远|优先|习惯|爱好|擅长|最爱|不喜欢/i,
  /[\w.-]+@[\w.-]+\.\w+/,
  /\+\d{10,}/,
  /(?:我|my)\s*(?:是|叫|名字|name|住在|live|来自|from|生日|birthday|电话|phone|邮箱|email)/i,
  /(?:我|i)\s*(?:喜欢|崇拜|讨厌|害怕|擅长|不会|爱|恨|想要|需要|希望|觉得|认为|相信)/i,
  /(?:favorite|favourite|love|hate|enjoy|dislike|admire|idol|fan of)/i,
];

const CJK_CHAR_REGEX = /[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]/;
const RELEVANT_MEMORIES_BLOCK_RE = /<relevant-memories>[\s\S]*?<\/relevant-memories>/gi;
const CONVERSATION_METADATA_BLOCK_RE =
  /(?:^|\n)\s*(?:Conversation info|Conversation metadata|会话信息|对话信息)\s*(?:\([^)]+\))?\s*:\s*```[\s\S]*?```/gi;
const FENCED_JSON_BLOCK_RE = /```json\s*([\s\S]*?)```/gi;
const METADATA_JSON_KEY_RE =
  /"(session|sessionid|sessionkey|conversationid|channel|sender|userid|agentid|timestamp|timezone)"\s*:/gi;
const LEADING_TIMESTAMP_PREFIX_RE = /^\s*\[[^\]\n]{1,120}\]\s*/;
const COMMAND_TEXT_RE = /^\/[a-z0-9_-]{1,64}\b/i;
const NON_CONTENT_TEXT_RE = /^[\p{P}\p{S}\s]+$/u;
const SUBAGENT_CONTEXT_RE = /^\s*\[Subagent Context\]/i;
const MEMORY_INTENT_RE = /记住|记下|remember|save|store|偏好|preference|规则|rule|事实|fact/i;
const QUESTION_CUE_RE =
  /[?？]|\b(?:what|when|where|who|why|how|which|can|could|would|did|does|is|are)\b|^(?:请问|能否|可否|怎么|如何|什么时候|谁|什么|哪|是否)/i;
const CAPTURE_LIMIT = 3;
const SPEAKER_TAG_RE = /(?:^|\s)([A-Za-z\u4e00-\u9fa5][A-Za-z0-9_\u4e00-\u9fa5-]{1,30}):\s/g;

function resolveCaptureMinLength(text: string): number {
  return CJK_CHAR_REGEX.test(text) ? 4 : 10;
}

function looksLikeMetadataJsonBlock(content: string): boolean {
  const matchedKeys = new Set<string>();
  const matches = content.matchAll(METADATA_JSON_KEY_RE);
  for (const match of matches) {
    const key = (match[1] ?? "").toLowerCase();
    if (key) {
      matchedKeys.add(key);
    }
  }
  return matchedKeys.size >= 3;
}

function sanitizeUserTextForCapture(text: string): string {
  return text
    .replace(RELEVANT_MEMORIES_BLOCK_RE, " ")
    .replace(CONVERSATION_METADATA_BLOCK_RE, " ")
    .replace(FENCED_JSON_BLOCK_RE, (full, inner) =>
      looksLikeMetadataJsonBlock(String(inner ?? "")) ? " " : full,
    )
    .replace(LEADING_TIMESTAMP_PREFIX_RE, "")
    .replace(/\u0000/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function looksLikeQuestionOnlyText(text: string): boolean {
  if (!QUESTION_CUE_RE.test(text) || MEMORY_INTENT_RE.test(text)) {
    return false;
  }
  // Multi-speaker transcripts often contain many "?" but should still be captured.
  const speakerTags = text.match(/[A-Za-z\u4e00-\u9fa5]{2,20}:\s/g) ?? [];
  if (speakerTags.length >= 2 || text.length > 280) {
    return false;
  }
  return true;
}

type TranscriptLikeIngestDecision = {
  shouldAssist: boolean;
  reason: string;
  normalizedText: string;
  speakerTurns: number;
  chars: number;
};

function countSpeakerTurns(text: string): number {
  let count = 0;
  for (const _match of text.matchAll(SPEAKER_TAG_RE)) {
    count += 1;
  }
  return count;
}

function isTranscriptLikeIngest(
  text: string,
  options: {
    minSpeakerTurns: number;
    minChars: number;
  },
): TranscriptLikeIngestDecision {
  const normalizedText = sanitizeUserTextForCapture(text.trim());
  if (!normalizedText) {
    return {
      shouldAssist: false,
      reason: "empty_text",
      normalizedText,
      speakerTurns: 0,
      chars: 0,
    };
  }

  if (COMMAND_TEXT_RE.test(normalizedText)) {
    return {
      shouldAssist: false,
      reason: "command_text",
      normalizedText,
      speakerTurns: 0,
      chars: normalizedText.length,
    };
  }

  if (SUBAGENT_CONTEXT_RE.test(normalizedText)) {
    return {
      shouldAssist: false,
      reason: "subagent_context",
      normalizedText,
      speakerTurns: 0,
      chars: normalizedText.length,
    };
  }

  if (NON_CONTENT_TEXT_RE.test(normalizedText)) {
    return {
      shouldAssist: false,
      reason: "non_content_text",
      normalizedText,
      speakerTurns: 0,
      chars: normalizedText.length,
    };
  }

  if (looksLikeQuestionOnlyText(normalizedText)) {
    return {
      shouldAssist: false,
      reason: "question_text",
      normalizedText,
      speakerTurns: 0,
      chars: normalizedText.length,
    };
  }

  const chars = normalizedText.length;
  if (chars < options.minChars) {
    return {
      shouldAssist: false,
      reason: "chars_below_threshold",
      normalizedText,
      speakerTurns: 0,
      chars,
    };
  }

  const speakerTurns = countSpeakerTurns(normalizedText);
  if (speakerTurns < options.minSpeakerTurns) {
    return {
      shouldAssist: false,
      reason: "speaker_turns_below_threshold",
      normalizedText,
      speakerTurns,
      chars,
    };
  }

  return {
    shouldAssist: true,
    reason: "transcript_like_ingest",
    normalizedText,
    speakerTurns,
    chars,
  };
}

function normalizeCaptureDedupeText(text: string): string {
  return normalizeDedupeText(text).replace(/[\p{P}\p{S}]+/gu, " ").replace(/\s+/g, " ").trim();
}

function pickRecentUniqueTexts(texts: string[], limit: number): string[] {
  if (limit <= 0 || texts.length === 0) {
    return [];
  }
  const seen = new Set<string>();
  const picked: string[] = [];
  for (let i = texts.length - 1; i >= 0; i -= 1) {
    const text = texts[i];
    const key = normalizeCaptureDedupeText(text);
    if (!key || seen.has(key)) {
      continue;
    }
    seen.add(key);
    picked.push(text);
    if (picked.length >= limit) {
      break;
    }
  }
  return picked.reverse();
}

function getCaptureDecision(text: string, mode: CaptureMode, captureMaxLength: number): {
  shouldCapture: boolean;
  reason: string;
  normalizedText: string;
} {
  const trimmed = text.trim();
  const normalizedText = sanitizeUserTextForCapture(trimmed);
  const hadSanitization = normalizedText !== trimmed;
  if (!normalizedText) {
    return {
      shouldCapture: false,
      reason: /<relevant-memories>/i.test(trimmed) ? "injected_memory_context_only" : "empty_text",
      normalizedText: "",
    };
  }

  const compactText = normalizedText.replace(/\s+/g, "");
  const minLength = resolveCaptureMinLength(compactText);
  if (compactText.length < minLength || normalizedText.length > captureMaxLength) {
    return {
      shouldCapture: false,
      reason: "length_out_of_range",
      normalizedText,
    };
  }

  if (COMMAND_TEXT_RE.test(normalizedText)) {
    return {
      shouldCapture: false,
      reason: "command_text",
      normalizedText,
    };
  }

  if (NON_CONTENT_TEXT_RE.test(normalizedText)) {
    return {
      shouldCapture: false,
      reason: "non_content_text",
      normalizedText,
    };
  }
  if (SUBAGENT_CONTEXT_RE.test(normalizedText)) {
    return {
      shouldCapture: false,
      reason: "subagent_context",
      normalizedText,
    };
  }
  if (looksLikeQuestionOnlyText(normalizedText)) {
    return {
      shouldCapture: false,
      reason: "question_text",
      normalizedText,
    };
  }

  if (mode === "keyword") {
    for (const trigger of MEMORY_TRIGGERS) {
      if (trigger.test(normalizedText)) {
        return {
          shouldCapture: true,
          reason: hadSanitization
            ? `matched_trigger_after_sanitize:${trigger.toString()}`
            : `matched_trigger:${trigger.toString()}`,
          normalizedText,
        };
      }
    }
    return {
      shouldCapture: false,
      reason: hadSanitization ? "no_trigger_matched_after_sanitize" : "no_trigger_matched",
      normalizedText,
    };
  }

  return {
    shouldCapture: true,
    reason: hadSanitization ? "semantic_candidate_after_sanitize" : "semantic_candidate",
    normalizedText,
  };
}

function clampScore(value: number | undefined): number {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return 0;
  }
  return Math.max(0, Math.min(1, value));
}

function isMemoryUri(uri: string): boolean {
  return MEMORY_URI_PATTERNS.some((pattern) => pattern.test(uri));
}

function normalizeDedupeText(text: string): string {
  return text.toLowerCase().replace(/\s+/g, " ").trim();
}

function isEventOrCaseMemory(item: FindResultItem): boolean {
  const category = (item.category ?? "").toLowerCase();
  const uri = item.uri.toLowerCase();
  return (
    category === "events" ||
    category === "cases" ||
    uri.includes("/events/") ||
    uri.includes("/cases/")
  );
}

function getMemoryDedupeKey(item: FindResultItem): string {
  const abstract = normalizeDedupeText(item.abstract ?? item.overview ?? "");
  const category = (item.category ?? "").toLowerCase() || "unknown";
  if (abstract && !isEventOrCaseMemory(item)) {
    return `abstract:${category}:${abstract}`;
  }
  return `uri:${item.uri}`;
}

function postProcessMemories(
  items: FindResultItem[],
  options: {
    limit: number;
    scoreThreshold: number;
    leafOnly?: boolean;
  },
): FindResultItem[] {
  const deduped: FindResultItem[] = [];
  const seen = new Set<string>();
  const sorted = [...items].sort((a, b) => clampScore(b.score) - clampScore(a.score));
  for (const item of sorted) {
    if (options.leafOnly && item.is_leaf !== true) {
      continue;
    }
    if (clampScore(item.score) < options.scoreThreshold) {
      continue;
    }
    const key = getMemoryDedupeKey(item);
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    deduped.push(item);
    if (deduped.length >= options.limit) {
      break;
    }
  }
  return deduped;
}

function formatMemoryLines(items: FindResultItem[]): string {
  return items
    .map((item, index) => {
      const score = clampScore(item.score);
      const abstract = item.abstract?.trim() || item.overview?.trim() || item.uri;
      const category = item.category ?? "memory";
      return `${index + 1}. [${category}] ${abstract} (${(score * 100).toFixed(0)}%)`;
    })
    .join("\n");
}

function trimForLog(value: string, limit = 260): string {
  const normalized = value.trim();
  if (normalized.length <= limit) {
    return normalized;
  }
  return `${normalized.slice(0, limit)}...`;
}

function toJsonLog(value: unknown, maxLen = 6000): string {
  try {
    const json = JSON.stringify(value);
    if (json.length <= maxLen) {
      return json;
    }
    return JSON.stringify({
      truncated: true,
      length: json.length,
      preview: `${json.slice(0, maxLen)}...`,
    });
  } catch {
    return JSON.stringify({ error: "stringify_failed" });
  }
}

function summarizeInjectionMemories(items: FindResultItem[]): Array<Record<string, unknown>> {
  return items.map((item) => ({
    uri: item.uri,
    category: item.category ?? null,
    abstract: trimForLog(item.abstract?.trim() || item.overview?.trim() || item.uri, 180),
    score: clampScore(item.score),
    is_leaf: item.is_leaf === true,
  }));
}

function summarizeExtractedMemories(
  items: Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  return items.slice(0, 10).map((item) => {
    const abstractRaw =
      typeof item.abstract === "string"
        ? item.abstract
        : typeof item.overview === "string"
          ? item.overview
          : typeof item.title === "string"
            ? item.title
            : "";
    return {
      uri: typeof item.uri === "string" ? item.uri : null,
      category: typeof item.category === "string" ? item.category : null,
      abstract: trimForLog(abstractRaw, 180),
      is_leaf: item.is_leaf === true,
    };
  });
}

function isPreferencesMemory(item: FindResultItem): boolean {
  return (
    item.category === "preferences" ||
    item.uri.includes("/preferences/") ||
    item.uri.endsWith("/preferences")
  );
}

function isEventMemory(item: FindResultItem): boolean {
  const category = (item.category ?? "").toLowerCase();
  return category === "events" || item.uri.includes("/events/");
}

function isLeafLikeMemory(item: FindResultItem): boolean {
  return item.is_leaf === true || item.uri.endsWith(".md");
}

const PREFERENCE_QUERY_RE = /prefer|preference|favorite|favourite|like|偏好|喜欢|爱好|更倾向/i;
const TEMPORAL_QUERY_RE =
  /when|what time|date|day|month|year|yesterday|today|tomorrow|last|next|什么时候|何时|哪天|几月|几年|昨天|今天|明天|上周|下周|上个月|下个月|去年|明年/i;
const QUERY_TOKEN_RE = /[a-z0-9]{2,}/gi;
const QUERY_TOKEN_STOPWORDS = new Set([
  "what",
  "when",
  "where",
  "which",
  "who",
  "whom",
  "whose",
  "why",
  "how",
  "did",
  "does",
  "is",
  "are",
  "was",
  "were",
  "the",
  "and",
  "for",
  "with",
  "from",
  "that",
  "this",
  "your",
  "you",
]);

type RecallQueryProfile = {
  tokens: string[];
  wantsPreference: boolean;
  wantsTemporal: boolean;
};

function buildRecallQueryProfile(query: string): RecallQueryProfile {
  const text = query.trim();
  const allTokens = text.toLowerCase().match(QUERY_TOKEN_RE) ?? [];
  const tokens = allTokens.filter((token) => !QUERY_TOKEN_STOPWORDS.has(token));
  return {
    tokens,
    wantsPreference: PREFERENCE_QUERY_RE.test(text),
    wantsTemporal: TEMPORAL_QUERY_RE.test(text),
  };
}

function lexicalOverlapBoost(tokens: string[], text: string): number {
  if (tokens.length === 0 || !text) {
    return 0;
  }
  const haystack = ` ${text.toLowerCase()} `;
  let matched = 0;
  for (const token of tokens.slice(0, 8)) {
    if (haystack.includes(` ${token} `) || haystack.includes(token)) {
      matched += 1;
    }
  }
  return Math.min(0.2, (matched / Math.min(tokens.length, 4)) * 0.2);
}

function rankForInjection(item: FindResultItem, query: RecallQueryProfile): number {
  // Keep ranking simple and stable: semantic score + light query-aware boosts.
  const baseScore = clampScore(item.score);
  const abstract = (item.abstract ?? item.overview ?? "").trim();
  const leafBoost = isLeafLikeMemory(item) ? 0.12 : 0;
  const eventBoost = query.wantsTemporal && isEventMemory(item) ? 0.1 : 0;
  const preferenceBoost = query.wantsPreference && isPreferencesMemory(item) ? 0.08 : 0;
  const overlapBoost = lexicalOverlapBoost(query.tokens, `${item.uri} ${abstract}`);
  return baseScore + leafBoost + eventBoost + preferenceBoost + overlapBoost;
}

function pickMemoriesForInjection(
  items: FindResultItem[],
  limit: number,
  queryText: string,
): FindResultItem[] {
  if (items.length === 0 || limit <= 0) {
    return [];
  }

  const query = buildRecallQueryProfile(queryText);
  const sorted = [...items].sort((a, b) => rankForInjection(b, query) - rankForInjection(a, query));
  const deduped: FindResultItem[] = [];
  const seen = new Set<string>();
  for (const item of sorted) {
    const abstractKey = (item.abstract ?? item.overview ?? "").trim().toLowerCase();
    const key = abstractKey || item.uri;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    deduped.push(item);
  }
  const leaves = deduped.filter((item) => isLeafLikeMemory(item));
  if (leaves.length >= limit) {
    return leaves.slice(0, limit);
  }

  const picked = [...leaves];
  const used = new Set(leaves.map((item) => item.uri));
  for (const item of deduped) {
    if (picked.length >= limit) {
      break;
    }
    if (used.has(item.uri)) {
      continue;
    }
    picked.push(item);
  }
  return picked;
}

class OpenVikingClient {
  private readonly resolvedSpaceByScope: Partial<Record<ScopeName, string>> = {};
  private runtimeIdentity: RuntimeIdentity | null = null;

  constructor(
    private readonly baseUrl: string,
    private readonly apiKey: string,
    private readonly timeoutMs: number,
  ) {}

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    try {
      const headers = new Headers(init.headers ?? {});
      if (this.apiKey) {
        headers.set("X-API-Key", this.apiKey);
      }
      if (init.body && !headers.has("Content-Type")) {
        headers.set("Content-Type", "application/json");
      }

      const response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers,
        signal: controller.signal,
      });

      const payload = (await response.json().catch(() => ({}))) as {
        status?: string;
        result?: T;
        error?: { code?: string; message?: string };
      };

      if (!response.ok || payload.status === "error") {
        const code = payload.error?.code ? ` [${payload.error.code}]` : "";
        const message = payload.error?.message ?? `HTTP ${response.status}`;
        throw new Error(`OpenViking request failed${code}: ${message}`);
      }

      return (payload.result ?? payload) as T;
    } finally {
      clearTimeout(timer);
    }
  }

  async healthCheck(): Promise<void> {
    await this.request<{ status: string }>("/health");
  }

  private async ls(uri: string): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      `/api/v1/fs/ls?uri=${encodeURIComponent(uri)}&output=original`,
    );
  }

  private async getRuntimeIdentity(): Promise<RuntimeIdentity> {
    if (this.runtimeIdentity) {
      return this.runtimeIdentity;
    }
    const fallback: RuntimeIdentity = { userId: "default", agentId: "default" };
    try {
      const status = await this.request<{ user?: unknown }>("/api/v1/system/status");
      const userId =
        typeof status.user === "string" && status.user.trim() ? status.user.trim() : "default";
      this.runtimeIdentity = { userId, agentId: "default" };
      return this.runtimeIdentity;
    } catch {
      this.runtimeIdentity = fallback;
      return fallback;
    }
  }

  private async resolveScopeSpace(scope: ScopeName): Promise<string> {
    const cached = this.resolvedSpaceByScope[scope];
    if (cached) {
      return cached;
    }

    const identity = await this.getRuntimeIdentity();
    const fallbackSpace =
      scope === "user" ? identity.userId : md5Short(`${identity.userId}${identity.agentId}`);
    const reservedDirs = scope === "user" ? USER_STRUCTURE_DIRS : AGENT_STRUCTURE_DIRS;
    const preferredSpace =
      scope === "user" ? identity.userId : md5Short(`${identity.userId}${identity.agentId}`);

    try {
      const entries = await this.ls(`viking://${scope}`);
      const spaces = entries
        .filter((entry) => entry?.isDir === true)
        .map((entry) => (typeof entry.name === "string" ? entry.name.trim() : ""))
        .filter((name) => name && !name.startsWith(".") && !reservedDirs.has(name));

      if (spaces.length > 0) {
        if (spaces.includes(preferredSpace)) {
          this.resolvedSpaceByScope[scope] = preferredSpace;
          return preferredSpace;
        }
        if (scope === "user" && spaces.includes("default")) {
          this.resolvedSpaceByScope[scope] = "default";
          return "default";
        }
        if (spaces.length === 1) {
          this.resolvedSpaceByScope[scope] = spaces[0]!;
          return spaces[0]!;
        }
      }
    } catch {
      // Fall back to identity-derived space when listing fails.
    }

    this.resolvedSpaceByScope[scope] = fallbackSpace;
    return fallbackSpace;
  }

  private async normalizeTargetUri(targetUri: string): Promise<string> {
    const trimmed = targetUri.trim().replace(/\/+$/, "");
    const match = trimmed.match(/^viking:\/\/(user|agent)(?:\/(.*))?$/);
    if (!match) {
      return trimmed;
    }
    const scope = match[1] as ScopeName;
    const rawRest = (match[2] ?? "").trim();
    if (!rawRest) {
      return trimmed;
    }
    const parts = rawRest.split("/").filter(Boolean);
    if (parts.length === 0) {
      return trimmed;
    }

    const reservedDirs = scope === "user" ? USER_STRUCTURE_DIRS : AGENT_STRUCTURE_DIRS;
    if (!reservedDirs.has(parts[0]!)) {
      return trimmed;
    }

    const space = await this.resolveScopeSpace(scope);
    return `viking://${scope}/${space}/${parts.join("/")}`;
  }

  async find(
    query: string,
    options: {
      targetUri: string;
      limit: number;
      scoreThreshold?: number;
      sessionId?: string;
    },
  ): Promise<FindResult> {
    const normalizedTargetUri = await this.normalizeTargetUri(options.targetUri);
    const body = {
      query,
      target_uri: normalizedTargetUri,
      limit: options.limit,
      score_threshold: options.scoreThreshold,
      session_id: options.sessionId,
    };
    return this.request<FindResult>("/api/v1/search/search", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async createSession(): Promise<string> {
    const result = await this.request<{ session_id: string }>("/api/v1/sessions", {
      method: "POST",
      body: JSON.stringify({}),
    });
    return result.session_id;
  }

  async addSessionMessage(sessionId: string, role: string, content: string): Promise<void> {
    await this.request<{ session_id: string }>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify({ role, content }),
      },
    );
  }

  async extractSessionMemories(sessionId: string): Promise<Array<Record<string, unknown>>> {
    return this.request<Array<Record<string, unknown>>>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/extract`,
      { method: "POST", body: JSON.stringify({}) },
    );
  }

  async deleteSession(sessionId: string): Promise<void> {
    await this.request(`/api/v1/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
  }

  async deleteUri(uri: string): Promise<void> {
    await this.request(`/api/v1/fs?uri=${encodeURIComponent(uri)}&recursive=false`, {
      method: "DELETE",
    });
  }
}

function extractTextsFromUserMessages(messages: unknown[]): string[] {
  const texts: string[] = [];
  for (const msg of messages) {
    if (!msg || typeof msg !== "object") {
      continue;
    }
    const msgObj = msg as Record<string, unknown>;
    if (msgObj.role !== "user") {
      continue;
    }
    const content = msgObj.content;
    if (typeof content === "string") {
      texts.push(content);
      continue;
    }
    if (Array.isArray(content)) {
      for (const block of content) {
        if (!block || typeof block !== "object") {
          continue;
        }
        const blockObj = block as Record<string, unknown>;
        if (blockObj.type === "text" && typeof blockObj.text === "string") {
          texts.push(blockObj.text);
        }
      }
    }
  }
  return texts;
}

function extractLatestUserText(messages: unknown[] | undefined): string {
  if (!messages || messages.length === 0) {
    return "";
  }
  const texts = extractTextsFromUserMessages(messages);
  for (let i = texts.length - 1; i >= 0; i -= 1) {
    const normalized = sanitizeUserTextForCapture(texts[i] ?? "");
    if (normalized) {
      return normalized;
    }
  }
  return "";
}

function waitForHealth(baseUrl: string, timeoutMs: number, intervalMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = () => {
      if (Date.now() > deadline) {
        reject(new Error(`OpenViking health check timeout at ${baseUrl}`));
        return;
      }
      fetch(`${baseUrl}/health`)
        .then((r) => r.json())
        .then((body: { status?: string }) => {
          if (body?.status === "ok") {
            resolve();
            return;
          }
          setTimeout(tick, intervalMs);
        })
        .catch(() => setTimeout(tick, intervalMs));
    };
    tick();
  });
}

const memoryPlugin = {
  id: "memory-openviking",
  name: "Memory (OpenViking)",
  description: "OpenViking-backed long-term memory with auto-recall/capture",
  kind: "memory" as const,
  configSchema: memoryOpenVikingConfigSchema,

  register(api: OpenClawPluginApi) {
    const cfg = memoryOpenVikingConfigSchema.parse(api.pluginConfig);
    const localCacheKey = `${cfg.mode}:${cfg.baseUrl}:${cfg.configPath}:${cfg.apiKey}`;

    let clientPromise: Promise<OpenVikingClient>;
    let localProcess: ReturnType<typeof spawn> | null = null;
    let resolveLocalClient: ((c: OpenVikingClient) => void) | null = null;

    if (cfg.mode === "local") {
      const cached = localClientCache.get(localCacheKey);
      if (cached) {
        localProcess = cached.process;
        clientPromise = Promise.resolve(cached.client);
      } else {
        clientPromise = new Promise<OpenVikingClient>((resolve) => {
          resolveLocalClient = resolve;
        });
      }
    } else {
      clientPromise = Promise.resolve(new OpenVikingClient(cfg.baseUrl, cfg.apiKey, cfg.timeoutMs));
    }

    const getClient = (): Promise<OpenVikingClient> => clientPromise;

    api.registerTool(
      {
        name: "memory_recall",
        label: "Memory Recall (OpenViking)",
        description:
          "Search long-term memories from OpenViking. Use when you need past user preferences, facts, or decisions.",
        parameters: Type.Object({
          query: Type.String({ description: "Search query" }),
          limit: Type.Optional(
            Type.Number({ description: "Max results (default: plugin config)" }),
          ),
          scoreThreshold: Type.Optional(
            Type.Number({ description: "Minimum score (0-1, default: plugin config)" }),
          ),
          targetUri: Type.Optional(
            Type.String({ description: "Search scope URI (default: plugin config)" }),
          ),
        }),
        async execute(_toolCallId, params) {
          const { query } = params as { query: string };
          const limit =
            typeof (params as { limit?: number }).limit === "number"
              ? Math.max(1, Math.floor((params as { limit: number }).limit))
              : cfg.recallLimit;
          const scoreThreshold =
            typeof (params as { scoreThreshold?: number }).scoreThreshold === "number"
              ? Math.max(0, Math.min(1, (params as { scoreThreshold: number }).scoreThreshold))
              : cfg.recallScoreThreshold;
          const targetUri =
            typeof (params as { targetUri?: string }).targetUri === "string"
              ? (params as { targetUri: string }).targetUri
              : cfg.targetUri;
          const requestLimit = Math.max(limit * 4, limit);
          const result = await (await getClient()).find(query, {
            targetUri,
            limit: requestLimit,
            scoreThreshold: 0,
          });
          const memories = postProcessMemories(result.memories ?? [], {
            limit,
            scoreThreshold,
          });
          if (memories.length === 0) {
            return {
              content: [{ type: "text", text: "No relevant OpenViking memories found." }],
              details: { count: 0, total: result.total ?? 0, scoreThreshold },
            };
          }
          return {
            content: [
              {
                type: "text",
                text: `Found ${memories.length} memories:\n\n${formatMemoryLines(memories)}`,
              },
            ],
            details: {
              count: memories.length,
              memories,
              total: result.total ?? memories.length,
              scoreThreshold,
              requestLimit,
            },
          };
        },
      },
      { name: "memory_recall" },
    );

    api.registerTool(
      {
        name: "memory_store",
        label: "Memory Store (OpenViking)",
        description:
          "Store text in OpenViking memory pipeline by writing to a session and running memory extraction.",
        parameters: Type.Object({
          text: Type.String({ description: "Information to store as memory source text" }),
          role: Type.Optional(Type.String({ description: "Session role, default user" })),
          sessionId: Type.Optional(Type.String({ description: "Existing OpenViking session ID" })),
        }),
        async execute(_toolCallId, params) {
          const { text } = params as { text: string };
          const role =
            typeof (params as { role?: string }).role === "string"
              ? (params as { role: string }).role
              : "user";
          const sessionIdIn = (params as { sessionId?: string }).sessionId;

          api.logger.info?.(
            `memory-openviking: memory_store invoked (textLength=${text?.length ?? 0}, sessionId=${sessionIdIn ?? "temp"})`,
          );

          let sessionId = sessionIdIn;
          let createdTempSession = false;
          try {
            const c = await getClient();
            if (!sessionId) {
              sessionId = await c.createSession();
              createdTempSession = true;
            }
            await c.addSessionMessage(sessionId, role, text);
            const extracted = await c.extractSessionMemories(sessionId);
            if (extracted.length === 0) {
              api.logger.warn(
                `memory-openviking: memory_store completed but extract returned 0 memories (sessionId=${sessionId}). ` +
                  "Check OpenViking server logs for embedding/extract errors (e.g. 401 API key, or extraction pipeline).",
              );
            } else {
              api.logger.info?.(`memory-openviking: memory_store extracted ${extracted.length} memories`);
            }
            return {
              content: [
                {
                  type: "text",
                  text: `Stored in OpenViking session ${sessionId} and extracted ${extracted.length} memories.`,
                },
              ],
              details: { action: "stored", sessionId, extractedCount: extracted.length, extracted },
            };
          } catch (err) {
            api.logger.warn(`memory-openviking: memory_store failed: ${String(err)}`);
            throw err;
          } finally {
            if (createdTempSession && sessionId) {
              const c = await getClient().catch(() => null);
              if (c) await c.deleteSession(sessionId!).catch(() => {});
            }
          }
        },
      },
      { name: "memory_store" },
    );

    api.registerTool(
      {
        name: "memory_forget",
        label: "Memory Forget (OpenViking)",
        description:
          "Forget memory by URI, or search then delete when a strong single match is found.",
        parameters: Type.Object({
          uri: Type.Optional(Type.String({ description: "Exact memory URI to delete" })),
          query: Type.Optional(Type.String({ description: "Search query to find memory URI" })),
          targetUri: Type.Optional(
            Type.String({ description: "Search scope URI (default: plugin config)" }),
          ),
          limit: Type.Optional(Type.Number({ description: "Search limit (default: 5)" })),
          scoreThreshold: Type.Optional(
            Type.Number({ description: "Minimum score (0-1, default: plugin config)" }),
          ),
        }),
        async execute(_toolCallId, params) {
          const uri = (params as { uri?: string }).uri;
          if (uri) {
            if (!isMemoryUri(uri)) {
              return {
                content: [{ type: "text", text: `Refusing to delete non-memory URI: ${uri}` }],
                details: { action: "rejected", uri },
              };
            }
            await (await getClient()).deleteUri(uri);
            return {
              content: [{ type: "text", text: `Forgotten: ${uri}` }],
              details: { action: "deleted", uri },
            };
          }

          const query = (params as { query?: string }).query;
          if (!query) {
            return {
              content: [{ type: "text", text: "Provide uri or query." }],
              details: { error: "missing_param" },
            };
          }

          const limit =
            typeof (params as { limit?: number }).limit === "number"
              ? Math.max(1, Math.floor((params as { limit: number }).limit))
              : 5;
          const scoreThreshold =
            typeof (params as { scoreThreshold?: number }).scoreThreshold === "number"
              ? Math.max(0, Math.min(1, (params as { scoreThreshold: number }).scoreThreshold))
              : cfg.recallScoreThreshold;
          const targetUri =
            typeof (params as { targetUri?: string }).targetUri === "string"
              ? (params as { targetUri: string }).targetUri
              : cfg.targetUri;
          const requestLimit = Math.max(limit * 4, 20);

          const result = await (await getClient()).find(query, {
            targetUri,
            limit: requestLimit,
            scoreThreshold: 0,
          });
          const candidates = postProcessMemories(result.memories ?? [], {
            limit: requestLimit,
            scoreThreshold,
            leafOnly: true,
          }).filter((item) => isMemoryUri(item.uri));
          if (candidates.length === 0) {
            return {
              content: [
                {
                  type: "text",
                  text: "No matching leaf memory candidates found. Try a more specific query.",
                },
              ],
              details: { action: "none", scoreThreshold },
            };
          }
          const top = candidates[0];
          if (candidates.length === 1 && clampScore(top.score) >= 0.85) {
            await (await getClient()).deleteUri(top.uri);
            return {
              content: [{ type: "text", text: `Forgotten: ${top.uri}` }],
              details: { action: "deleted", uri: top.uri, score: top.score ?? 0 },
            };
          }

          const list = candidates
            .map((item) => `- ${item.uri} (${(clampScore(item.score) * 100).toFixed(0)}%)`)
            .join("\n");

          return {
            content: [
              {
                type: "text",
                text: `Found ${candidates.length} candidates. Specify uri:\n${list}`,
              },
            ],
            details: { action: "candidates", candidates, scoreThreshold, requestLimit },
          };
        },
      },
      { name: "memory_forget" },
    );

    if (cfg.autoRecall || cfg.ingestReplyAssist) {
      api.on("before_agent_start", async (event) => {
        const queryText = extractLatestUserText(event.messages) || event.prompt.trim();
        if (!queryText) {
          return;
        }
        const prependContextParts: string[] = [];

        if (cfg.autoRecall && queryText.length >= 5) {
          try {
            const candidateLimit = Math.max(cfg.recallLimit * 4, cfg.recallLimit);
            const result = await (await getClient()).find(queryText, {
              targetUri: cfg.targetUri,
              limit: candidateLimit,
              scoreThreshold: 0,
            });
            const processed = postProcessMemories(result.memories ?? [], {
              limit: candidateLimit,
              scoreThreshold: cfg.recallScoreThreshold,
            });
            const memories = pickMemoriesForInjection(processed, cfg.recallLimit, queryText);
            if (memories.length > 0) {
              const memoryContext = memories
                .map((item) => `- [${item.category ?? "memory"}] ${item.abstract ?? item.uri}`)
                .join("\n");
              api.logger.info?.(
                `memory-openviking: injecting ${memories.length} memories into context`,
              );
              api.logger.info?.(
                `memory-openviking: inject-detail ${toJsonLog({ count: memories.length, memories: summarizeInjectionMemories(memories) })}`,
              );
              prependContextParts.push(
                "<relevant-memories>\nThe following OpenViking memories may be relevant:\n" +
                  `${memoryContext}\n` +
                  "</relevant-memories>",
              );
            }
          } catch (err) {
            api.logger.warn(`memory-openviking: auto-recall failed: ${String(err)}`);
          }
        }

        if (cfg.ingestReplyAssist) {
          const decision = isTranscriptLikeIngest(queryText, {
            minSpeakerTurns: cfg.ingestReplyAssistMinSpeakerTurns,
            minChars: cfg.ingestReplyAssistMinChars,
          });
          if (decision.shouldAssist) {
            api.logger.info?.(
              `memory-openviking: ingest-reply-assist applied (reason=${decision.reason}, speakerTurns=${decision.speakerTurns}, chars=${decision.chars})`,
            );
            prependContextParts.push(
              "<ingest-reply-assist>\n" +
                "The latest user input looks like a multi-speaker transcript used for memory ingestion.\n" +
                "Reply with 1-2 concise sentences to acknowledge or summarize key points.\n" +
                "Do not output NO_REPLY or an empty reply.\n" +
                "Do not fabricate facts beyond the provided transcript and recalled memories.\n" +
                "</ingest-reply-assist>",
            );
          }
        }

        if (prependContextParts.length > 0) {
          return {
            prependContext: prependContextParts.join("\n\n"),
          };
        }
      });
    }

    if (cfg.autoCapture) {
      api.on("agent_end", async (event) => {
        if (!event.success || !event.messages || event.messages.length === 0) {
          api.logger.info(
            `memory-openviking: auto-capture skipped (success=${String(event.success)}, messages=${event.messages?.length ?? 0})`,
          );
          return;
        }
        try {
          const texts = extractTextsFromUserMessages(event.messages);
          api.logger.info(
            `memory-openviking: auto-capture evaluating ${texts.length} text candidates`,
          );
          const decisions = texts
            .map((text) => {
              const decision = getCaptureDecision(text, cfg.captureMode, cfg.captureMaxLength);
              return {
                captureText: decision.normalizedText,
                decision,
              };
            })
            .filter((item) => item.captureText);
          for (const item of decisions.slice(0, 5)) {
            const preview =
              item.captureText.length > 80
                ? `${item.captureText.slice(0, 80)}...`
                : item.captureText;
            api.logger.info(
              `memory-openviking: capture-check shouldCapture=${String(item.decision.shouldCapture)} reason=${item.decision.reason} text="${preview}"`,
            );
          }
          const toCapture = decisions
            .filter((item) => item.decision.shouldCapture)
            .map((item) => item.captureText);
          const selected = pickRecentUniqueTexts(toCapture, CAPTURE_LIMIT);
          if (selected.length === 0) {
            api.logger.info("memory-openviking: auto-capture skipped (no matched texts)");
            return;
          }
          const c = await getClient();
          const sessionId = await c.createSession();
          try {
            for (const text of selected) {
              await c.addSessionMessage(sessionId, "user", text);
            }
            const extracted = await c.extractSessionMemories(sessionId);
            api.logger.info(
              `memory-openviking: auto-captured ${selected.length} messages, extracted ${extracted.length} memories`,
            );
            api.logger.info(
              `memory-openviking: capture-detail ${toJsonLog({
                capturedCount: selected.length,
                captured: selected.map((text) => trimForLog(text, 260)),
                extractedCount: extracted.length,
                extracted: summarizeExtractedMemories(extracted),
              })}`,
            );
            if (extracted.length === 0) {
              api.logger.warn(
                "memory-openviking: auto-capture completed but extract returned 0 memories. Check OpenViking server logs for embedding/extract errors.",
              );
            }
          } finally {
            await c.deleteSession(sessionId).catch(() => {});
          }
        } catch (err) {
          api.logger.warn(`memory-openviking: auto-capture failed: ${String(err)}`);
        }
      });
    }

    api.registerService({
      id: "memory-openviking",
      start: async () => {
        if (cfg.mode === "local" && resolveLocalClient) {
          const baseUrl = cfg.baseUrl;
          // Local mode: startup (embedder load, AGFS) can take 1–2 min; use longer health timeout
          const timeoutMs = Math.max(cfg.timeoutMs, 120_000);
          const intervalMs = 500;
          const defaultPy = IS_WIN ? "python" : "python3";
          let pythonCmd = process.env.OPENVIKING_PYTHON;
          if (!pythonCmd) {
            if (IS_WIN) {
              const envBat = join(homedir(), ".openclaw", "openviking.env.bat");
              if (existsSync(envBat)) {
                try {
                  const content = readFileSync(envBat, "utf-8");
                  const m = content.match(/set\s+OPENVIKING_PYTHON=(.+)/i);
                  if (m?.[1]) pythonCmd = m[1].trim();
                } catch { /* ignore */ }
              }
            } else {
              const envFile = join(homedir(), ".openclaw", "openviking.env");
              if (existsSync(envFile)) {
                try {
                  const content = readFileSync(envFile, "utf-8");
                  const m = content.match(/OPENVIKING_PYTHON=['"]([^'"]+)['"]/);
                  if (m?.[1]) pythonCmd = m[1];
                } catch {
                  /* ignore */
                }
              }
            }
          }
          if (!pythonCmd) {
            if (IS_WIN) {
              try {
                pythonCmd = execSync("where python", { encoding: "utf-8", shell: true }).split(/\r?\n/)[0].trim();
              } catch {
                pythonCmd = "python";
              }
            } else {
              try {
                pythonCmd = execSync("command -v python3 || which python3", {
                  encoding: "utf-8",
                  env: process.env,
                  shell: "/bin/sh",
                }).trim();
              } catch {
                pythonCmd = "python3";
              }
            }
          }
          if (pythonCmd === defaultPy) {
            api.logger.warn?.(
              `memory-openviking: 未解析到 ${defaultPy} 路径，将用 "${defaultPy}"。若 openviking 在自定义 Python 下，请设置 OPENVIKING_PYTHON` +
              (IS_WIN ? ' 或 call "%USERPROFILE%\\.openclaw\\openviking.env.bat"' : " 或 source ~/.openclaw/openviking.env"),
            );
          }
          // Kill stale OpenViking processes occupying the target port
          if (IS_WIN) {
            try {
              const netstatOut = execSync(`netstat -ano | findstr "LISTENING" | findstr ":${cfg.port}"`, {
                encoding: "utf-8", shell: true,
              }).trim();
              if (netstatOut) {
                const pids = new Set<number>();
                for (const line of netstatOut.split(/\r?\n/)) {
                  const m = line.trim().match(/\s(\d+)\s*$/);
                  if (m) pids.add(Number(m[1]));
                }
                for (const pid of pids) {
                  if (pid > 0) {
                    api.logger.info?.(`memory-openviking: killing stale process on port ${cfg.port} (pid ${pid})`);
                    try { execSync(`taskkill /PID ${pid} /F`, { shell: true }); } catch { /* already gone */ }
                  }
                }
                await new Promise((r) => setTimeout(r, 500));
              }
            } catch { /* netstat not available or no stale process */ }
          } else {
            try {
              const lsofOut = execSync(`lsof -ti tcp:${cfg.port} -s tcp:listen 2>/dev/null || true`, {
                encoding: "utf-8",
                shell: "/bin/sh",
              }).trim();
              if (lsofOut) {
                for (const pidStr of lsofOut.split(/\s+/)) {
                  const pid = Number(pidStr);
                  if (pid > 0) {
                    api.logger.info?.(`memory-openviking: killing stale process on port ${cfg.port} (pid ${pid})`);
                    try { process.kill(pid, "SIGKILL"); } catch { /* already gone */ }
                  }
                }
                await new Promise((r) => setTimeout(r, 500));
              }
            } catch { /* lsof not available or no stale process */ }
          }

          // Inherit system environment; optionally override Go/Python paths via env vars
          const pathSep = IS_WIN ? ";" : ":";
          const env = {
            ...process.env,
            OPENVIKING_CONFIG_FILE: cfg.configPath,
            ...(process.env.OPENVIKING_GO_PATH && { PATH: `${process.env.OPENVIKING_GO_PATH}${pathSep}${process.env.PATH || ""}` }),
            ...(process.env.OPENVIKING_GOPATH && { GOPATH: process.env.OPENVIKING_GOPATH }),
            ...(process.env.OPENVIKING_GOPROXY && { GOPROXY: process.env.OPENVIKING_GOPROXY }),
          };
          const child = spawn(
            pythonCmd,
            [
              "-m",
              "openviking.server.bootstrap",
              "--config",
              cfg.configPath,
              "--host",
              "127.0.0.1",
              "--port",
              String(cfg.port),
            ],
            { env, cwd: IS_WIN ? tmpdir() : "/tmp", stdio: ["ignore", "pipe", "pipe"] },
          );
          localProcess = child;
          child.on("error", (err) => api.logger.warn(`memory-openviking: local server error: ${String(err)}`));
          child.stderr?.on("data", (chunk) => api.logger.debug?.(`[openviking] ${String(chunk).trim()}`));
          try {
            await waitForHealth(baseUrl, timeoutMs, intervalMs);
            const client = new OpenVikingClient(baseUrl, cfg.apiKey, cfg.timeoutMs);
            localClientCache.set(localCacheKey, { client, process: child });
            resolveLocalClient(client);
            api.logger.info(
              `memory-openviking: local server started (${baseUrl}, config: ${cfg.configPath})`,
            );
          } catch (err) {
            localProcess = null;
            child.kill("SIGTERM");
            throw err;
          }
        } else {
          await (await getClient()).healthCheck().catch(() => {});
          api.logger.info(
            `memory-openviking: initialized (url: ${cfg.baseUrl}, targetUri: ${cfg.targetUri}, search: hybrid endpoint)`,
          );
        }
      },
      stop: () => {
        if (localProcess) {
          localProcess.kill("SIGTERM");
          localClientCache.delete(localCacheKey);
          localProcess = null;
          api.logger.info("memory-openviking: local server stopped");
        } else {
          api.logger.info("memory-openviking: stopped");
        }
      },
    });
  },
};

export default memoryPlugin;
