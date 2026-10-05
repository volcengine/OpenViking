export type Group = { dir: string; files: string[] };
export type Inject = { at: string; source: string; profile: string[]; groups: Group[] };
export type Lookup = {
  id: string;
  tool: string;
  kind: "read" | "write";
  query: string;
  target: string;
  uris: string[];
  isDone: boolean;
  isError: boolean;
};
// One OpenViking call Claude made while writing an answer. uris: the files a
// read named, or the results a search returned.
export type TurnLookup = {
  id: string;
  tool: string;
  kind: "read" | "write";
  query: string;
  uris: string[];
  // relevance a search gave each result (0–1); absent for reads
  scores?: Record<string, number>;
  isError: boolean;
};
// The reply row an answer's card is drawn under: its transcript id, and a
// fingerprint of the end of its text as a fallback match (never the text).
export type Reply = { id: string; tails: string[]; n: number };
// The conversation's first prompt row, which the overview card is drawn above:
// its transcript id once known, and a fingerprint of its text as a fallback match.
export type Opening = { id: string; text: string };
// One <memory> item of a per-prompt recall. layer: L0 abstract, L1 overview, L2 full, URI hint only.
export type RecallItem = {
  uri: string;
  layer: "L0" | "L1" | "L2" | "URI";
  score: number;
  tokens: number;
  summary: string;
  firstTurn: number;
};
export type Turn = {
  n: number;
  query: string;
  at: string;
  latencyMs: number | null;
  tokensUsed: number | null;
  items: RecallItem[];
  mutedHits: number;
  // searches and reads Claude ran for this answer
  lookups: TurnLookup[];
  // last-recall.json reason: ok, disabled, no_results, short_query, offline, ...
  reason: string;
};
export type Seen = { uri: string; turn: number; tokens: number };
// Totals across every session since ov-usage was installed: prompts recall
// added memories to, memory updates committed, and conversations that committed any.
export type Lifetime = {
  recalls: number;
  commits: number;
  conversations: number;
};

// Effective plugin settings from OpenViking's own config loader. Never holds the API key.
export type OvSettings = {
  server: string;
  apiKeySet: boolean;
  autoRecall: boolean;
  scoreThreshold: number;
  recallLimit: number;
  recallTokenBudget: number;
  recallPeerScope: string;
  autoCapture: boolean;
  commitTokenThreshold: number;
  startupInject: boolean;
  profileTokenBudget: number;
  mcpEnabled: boolean;
  error: string | null;
};

declare module "claude-code" {
  interface PluginState {
    "ov-usage": {
      inject: Inject | null;
      lookups: Lookup[];
      turn: Turn | null;
      turnCount: number;
      seen: Seen[];
      muted: string[];
      lifetime: Lifetime | null;
      showWeak: boolean;
      showDetails: boolean;
      showSettings: boolean;
      // EN / 中文 / follow the system; sysLang is what the system resolved to
      langPref: "en" | "zh" | "system";
      sysLang: "en" | "zh";
      // every recalled turn this session, oldest first (last 50), and which one the pane shows
      history: Turn[];
      viewTurn: number | null;
      showHistory: boolean;
      settings: OvSettings | null;
      // where the details show: the sidebar pane, or cards in the conversation
      layout: "pane" | "inline";
      // answer cards: titles only (low) or with each source's URI (high), and whether past the top 10
      cardDetail: "low" | "high";
      showAllCard: boolean;
      replies: Reply[];
      opening: Opening | null;
    };
  }
}
