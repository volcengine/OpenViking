export type ContextSearchBody = {
  query: string;
  mode: "context";
  purpose: "coding";
  score_threshold: number;
  quotas?: Record<string, number>;
  max_tokens?: number;
  peer_scope?: "actor";
  session_id?: string;
  query_expansion?: "off" | "auto";
  dedup_turns?: number;
  exclude_uris?: string[];
  rewrite?: boolean | "auto";
  rewrite_max_bullets?: number;
};

export type NormalizedContextEntry = {
  uri: string;
  category: string;
  detail: string;
  score: number;
  text: string;
};

export function buildContextSearchBody(
  cfg?: Record<string, unknown>,
  options?: {
    sessionId?: string;
    excludeUris?: string[];
    localCompressorAvailable?: boolean;
  },
): ContextSearchBody;

export function contextRequestTimeoutMs(
  cfg?: Record<string, unknown>,
  body?: Record<string, unknown>,
): number | undefined;

export function normalizeContextEntry(entry?: unknown): NormalizedContextEntry;

export type RecallFetchJSON = (
  path: string,
  init?: any,
  options?: any,
) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>;

export type RecallOptions = {
  actorPeerId?: string;
  legacyPeerId?: string;
  sessionId?: string;
  log?: (stage: string, data?: any) => void;
};

export type DetailedRecall = {
  block: string;
  contentCount: number;
  hintCount: number;
  budgetUsed: number;
  stage: "server_assembled" | "ranked" | "no_results" | "filtered_out";
};

export function buildRecallBlockDetailed(
  fetchJSON: RecallFetchJSON,
  cfg: Record<string, any>,
  query: string,
  options?: RecallOptions,
): Promise<DetailedRecall>;

export function buildRecallBlock(
  fetchJSON: RecallFetchJSON,
  cfg: Record<string, any>,
  query: string,
  options?: RecallOptions,
): Promise<string | null>;

export function buildRecallEndpointBody(cfg?: Record<string, any>): Record<string, any>;
export function estimateTokens(text: string): number;
export function isRecallEnabled(cfg?: Record<string, any>): boolean;
