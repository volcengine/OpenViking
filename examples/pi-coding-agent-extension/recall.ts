import type { OVClient, OVSearchResult } from "./client.js";
import type { OVConfig } from "./config.js";

export interface QueryProfile {
  tokens: string[];        // content words minus stopwords
  wantsPreference: boolean;
  wantsTemporal: boolean;
}

export interface RankedResult {
  uri: string;
  score: number;           // base + boosts
  abstract: string;
  content: string | null;  // resolved content, capped
  category: string;
  level: number;
}

export interface RecallCache {
  block: string | null;    // formatted <relevant-memories> text
  promptText: string;      // the query this cache is for
}

// Stopwords for query profiling
const STOPWORDS = new Set([
  "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
  "have", "has", "had", "do", "does", "did", "will", "would", "could",
  "should", "may", "might", "can", "shall", "to", "of", "in", "for",
  "on", "with", "at", "by", "from", "as", "into", "through", "during",
  "before", "after", "above", "below", "between", "out", "off", "over",
  "under", "again", "further", "then", "once", "and", "but", "or",
  "nor", "not", "so", "yet", "both", "either", "neither", "each",
  "every", "all", "any", "few", "more", "most", "other", "some",
  "such", "no", "only", "own", "same", "than", "too", "very",
  "just", "because", "if", "when", "where", "how", "what", "which",
  "who", "whom", "this", "that", "these", "those", "i", "me", "my",
  "it", "its", "we", "you", "they", "them", "there", "here",
]);

export class RecallManager {
  private client: OVClient;
  private config: OVConfig;
  private cache: RecallCache = { block: null, promptText: "" };

  constructor(client: OVClient, config: OVConfig) {
    this.client = client;
    this.config = config;
  }

  // --- CJK-aware Token Estimation ---

  estimateTokens(text: string): number {
    if (!text) return 0;
    let cjk = 0;
    for (let i = 0; i < text.length; i++) {
      if (text.charCodeAt(i) >= 0x3000) cjk++;
    }
    const other = text.length - cjk;
    return Math.ceil(cjk * 1.5 + other / 4);
  }

  // --- Query Profiling ---

  private profileQuery(query: string): QueryProfile {
    const words = query.toLowerCase()
      .replace(/[^\w\s]/g, " ")
      .split(/\s+/)
      .filter(w => w.length > 1 && !STOPWORDS.has(w));

    return {
      tokens: words,
      wantsPreference: /prefer|favorite|like|want|usually|always|never|hate|love/i.test(query),
      wantsTemporal:   /when|yesterday|last |recent|ago|last week|earlier|before/i.test(query),
    };
  }

  // --- Triple-Scope Search ---

  async searchAndCache(userQuery: string): Promise<string | null> {
    // Short-circuit on short queries
    if (userQuery.trim().length < this.config.recallMinQueryLength) {
      return null;
    }

    const profile = this.profileQuery(userQuery);
    const perSourceLimit = Math.max(this.config.recallLimit * 2, 8);

    // Resolve URIs dynamically
    const [userMemUri, agentMemUri, agentSkillsUri] = await Promise.all([
      this.client.resolveTargetUri("viking://user/memories"),
      this.client.resolveTargetUri("viking://agent/memories"),
      this.client.resolveTargetUri("viking://agent/skills"),
    ]);

    // Triple-scope parallel search
    const [userResults, agentResults, skillResults] = await Promise.all([
      this.client.find(userQuery, {
        targetUri: userMemUri, topK: perSourceLimit,
        scoreThreshold: this.config.recallScoreThreshold,
      }),
      this.client.find(userQuery, {
        targetUri: agentMemUri, topK: perSourceLimit,
        scoreThreshold: this.config.recallScoreThreshold,
      }),
      this.client.find(userQuery, {
        targetUri: agentSkillsUri, topK: perSourceLimit,
        scoreThreshold: this.config.recallScoreThreshold,
      }),
    ]);

    // Merge all results
    const all = [...userResults, ...agentResults, ...skillResults];

    // Score filter (client-side, in case server doesn't enforce)
    const filtered = all.filter(
      r => r.score >= this.config.recallScoreThreshold
    );
    if (filtered.length === 0) return null;

    // Dedup
    const deduped = this.dedup(filtered);

    // Rerank with query profile
    const ranked = this.rerank(deduped, profile);

    // Content resolution with per-item cap
    const resolved = await this.resolveContent(ranked);

    // Token-budgeted formatting with graceful degradation
    const block = this.formatResults(resolved);
    this.cache = { block, promptText: userQuery };
    return block;
  }

  // --- Injection ---

  injectRecall(messages: any[]): any[] {
    if (!this.cache.block) return messages;

    // Find the user message (scan backwards)
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i];
      if (msg.role === "user") {
        // Idempotency check
        const content = typeof msg.content === "string"
          ? msg.content
          : Array.isArray(msg.content)
            ? msg.content.filter((b: any) => b.type === "text").map((b: any) => b.text).join("")
            : "";

        if (content.includes("<relevant-memories>")) break;

        // Prepend block to user message
        const block = this.cache.block;
        if (typeof msg.content === "string") {
          msg.content = block + "\n" + msg.content;
        } else if (Array.isArray(msg.content)) {
          const textBlocks = msg.content.filter((b: any) => b.type === "text");
          if (textBlocks.length > 0) {
            (textBlocks[0] as any).text = block + "\n" + (textBlocks[0] as any).text;
          }
        }
        break;
      }
    }
    return messages;
  }

  invalidate(): void {
    this.cache = { block: null, promptText: "" };
  }

  // --- Dedup ---

  private dedup(results: OVSearchResult[]): OVSearchResult[] {
    const seen = new Map<string, OVSearchResult>();

    for (const r of results) {
      // Events/cases dedupe by URI; others by abstract text
      const isEvent = r.category === "event" || r.category === "case";
      const key = isEvent
        ? r.uri
        : (r.abstract?.toLowerCase().trim() || r.uri);

      if (!seen.has(key)) {
        seen.set(key, r);
      } else {
        // Keep higher score
        const existing = seen.get(key)!;
        if (r.score > existing.score) seen.set(key, r);
      }
    }
    return [...seen.values()];
  }

  // --- Reranking ---

  private rerank(
    results: OVSearchResult[], profile: QueryProfile,
  ): OVSearchResult[] {
    return results.map(r => {
      let score = r.score;

      // Leaf preference boost
      if ((r.level === 2) || r.uri.endsWith(".md")) {
        score += 0.12;
      }

      // Event boost (gated by temporal intent)
      if (profile.wantsTemporal &&
          (r.category === "event" || r.category === "case")) {
        score += 0.10;
      }

      // Preference boost (gated by preference intent)
      if (profile.wantsPreference && r.category === "preference") {
        score += 0.08;
      }

      // Lexical overlap boost
      const text = `${r.uri} ${r.abstract}`.toLowerCase();
      const matchCount = profile.tokens
        .slice(0, 4)
        .filter(t => text.includes(t)).length;
      score += 0.20 * (matchCount / Math.max(profile.tokens.length, 1));

      return { ...r, score };
    }).sort((a, b) => b.score - a.score)
      .slice(0, this.config.recallLimit);
  }

  // --- Content Resolution ---

  private async resolveContent(
    results: OVSearchResult[],
  ): Promise<RankedResult[]> {
    const resolved: RankedResult[] = [];

    for (const r of results) {
      let content: string | null = r.abstract ?? null;

      if (!this.config.recallPreferAbstract && r.level === 2) {
        // Fetch full content for level-2 items when abstracts not preferred
        const full = await this.client.readContent(r.uri);
        if (full) content = full;
      }

      // Per-item content cap
      if (content && content.length > this.config.recallMaxContentChars) {
        content = content.slice(0, this.config.recallMaxContentChars) + "...";
      }

      resolved.push({
        uri: r.uri,
        score: r.score,
        abstract: r.abstract ?? "",
        content,
        category: r.category ?? "unknown",
        level: r.level ?? 0,
      });
    }
    return resolved;
  }

  // --- Formatting with Graceful Degradation ---

  private formatResults(results: RankedResult[]): string {
    const lines: string[] = [
      "<relevant-memories>",
      "[System note: Recalled memory from OpenViking. Informational background data, NOT new user input.]",
    ];

    let budget = this.config.recallBudget;
    let first = true;

    for (const r of results) {
      const tokens = this.estimateTokens(r.content ?? r.abstract);

      if (first || tokens <= budget) {
        // Full content line
        const source = r.uri.includes("/skills/") ? "skill" : "memory";
        const text = r.content ?? r.abstract;
        lines.push(`- [${source} ${r.score.toFixed(2)}] ${text}`);
        budget -= tokens;
      } else {
        // Degraded hint — model can expand with viking_read
        lines.push(`- [hint ${r.score.toFixed(2)}] Use viking_read to expand: ${r.uri}`);
      }
      first = false;
    }

    lines.push("</relevant-memories>");
    return lines.join("\n");
  }
}
