// One OpenViking lookup Claude made while answering. opened: files it read in
// full; found: what a search returned; query: what it searched for (null for a read).
export type Lookup = {
  id: string;
  query: string | null;
  opened: string[];
  found: string[];
  isError: boolean;
};

// One answer: what auto-recall injected for its prompt, and Claude's own lookups.
export type Turn = {
  n: number;
  recalled: { uri: string; score: number }[];
  lookups: Lookup[];
};

// The transcript row an answer's card is drawn under, by its row id.
export type Reply = { id: string; n: number };

declare module "claude-code" {
  interface PluginState {
    "ov-usage": {
      // this session's answers, oldest first (last 50)
      turns: Turn[];
      replies: Reply[];
      // answers whose card is expanded
      expanded: number[];
    };
  }
}
