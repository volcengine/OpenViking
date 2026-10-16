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

// The transcript row an answer's card is drawn under: its row id (the terminal's
// requestId) and a hash of its text, since the desktop names rows by API message id.
export type Reply = { id: string; n: number; text?: string };

declare module "claude-code" {
  interface PluginState {
    "openviking-memory": {
      // this session's answers, oldest first (last 50)
      turns: Turn[];
      replies: Reply[];
      // answers whose card is expanded
      expanded: number[];
    };
  }
}
