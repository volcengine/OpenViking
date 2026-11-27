/**
 * What a host module (`hosts/<id>/host.mjs`) hands the flow. It describes the
 * host and decides nothing: which requests are sent, when a session commits and
 * what the model is told are the flow's.
 */

/** What a host event means to the flow. */
export type Stage =
  /** A session opens, resumes, or restarts after a compaction. */
  | "start"
  /** The user sent a prompt. */
  | "prompt"
  /** A turn finished; the transcript holds it. */
  | "turnEnd"
  /** The host is about to compact the context. */
  | "compactSoon"
  /** The session is over. */
  | "end"
  /** The host compacted and reports it in an event of its own. */
  | "compacted"
  /** A tool is about to run. */
  | "toolCall"
  /** A tool finished and the event carries something worth a note. */
  | "toolResult";

export interface HostFacts {
  /** Where the profile is injected. Default "start"; "firstPrompt" where only a prompt's answer reaches the model. */
  profileAt?: "start" | "firstPrompt";
  /** The host's compaction drops the profile the session started with. */
  compactionDropsProfile?: boolean;
  /** "default": the plugin fills session_history's `session`; "explicit": the model must pass it. */
  historySession?: "default" | "explicit";
  /** How the session-history note names the tool, when not the plain MCP name. */
  historyTool?: string;
  /**
   * false for a host with no transcript file: `decode` builds turns from the payload and notes.
   * "optional" where the file may be absent: `decode` then gets "" and does the same.
   */
  transcript?: false | "optional";
  /** How many queued writes a session start replays; the queue's own default when absent. */
  replayLimit?: number;
}

/** What one event's payload says. Pure: `io.text` is the only way to look at a file. */
export interface EventRead {
  /** The host's own id for the session; the payload's usual spellings are tried when absent. */
  sessionId?: string;
  /** The OpenViking session, for a host that derives it its own way; `sessionPrefix` + the host's id otherwise. */
  session?: string;
  /**
   * The event belongs to a child of the session (a subagent), named so that it
   * is unique within the parent: it is captured into an OpenViking session of
   * its own, and not at all with `skipSubagentSessions`.
   */
  child?: string;
  /** The session's directory; the payload's usual spellings are tried when absent. */
  cwd?: string;
  /** Leave this event alone, with the reason to log. */
  skip?: string;
  /** The user's prompt, cleaned of host markup. */
  prompt?: string;
  /** Identifies a prompt event the host may deliver twice. */
  eventId?: string;
  /** What `locate` needs to find the transcript; usually its path. */
  transcript?: unknown;
  /** The context was just compacted or the session resumed. */
  resumed?: "compact" | "resume" | "";
  /** This event's answer does not reach the model: leave what it owes for the next prompt. */
  defer?: boolean;
  /** The latest compaction a host without a compaction event knows of. */
  compaction?: unknown;
  /** Something this event carried that the transcript will not hold; `decode` gets it until its turns are settled. */
  note?: Record<string, unknown>;
}

export interface Turn {
  role: string;
  /** The turn's text. */
  content: string;
  /** The host's id for the turn, sent as `turn_id`. */
  turnId?: string;
  /** The host message, when the turn has tool calls or results beside its text. */
  payload?: { role: string; content: unknown };
}

export interface Decoded {
  /** The turns not yet acknowledged, oldest first. */
  turns: Turn[];
  /** The cursor to store once the first `count` turns are settled. */
  cursorAfter(count: number): unknown;
  /** Tool names by call id, for results that answer a call in an earlier turn. */
  toolNames?: Record<string, string>;
}

export interface DecodeInput {
  /** The transcript, or "" for a host without one. */
  text: string;
  /** What `cursorAfter` returned last; the session's stored state before the first capture. */
  cursor: any;
  notes: Array<Record<string, unknown>>;
  payload: Record<string, any>;
  cfg: Record<string, any>;
}

/** What a tool call is: one for the URI guard to look at, or a session_history call. */
export type ToolCall =
  | { name: string; input: Record<string, any> }
  | { history: unknown };

/** What the flow answers an event with. */
export interface Output {
  /** Context for the model. */
  context?: string;
  /** The tool call must not run; the reason is for the model. */
  deny?: string;
  /** The tool call runs; the model is told this. */
  notice?: string;
  /** The tool call runs with this input. */
  input?: Record<string, any>;
}

/** What an event did, as `observe` is told. */
export interface Outcome {
  /** The event was left alone: "disabled", "bypass", "subagent", or the host's own reason. */
  skip?: string;
  /** The context the event answered with. */
  context?: string;
  /** What the recall core reported for a prompt, and how long the search took. */
  recall?: { stage: string; contentCount?: number; hintCount?: number; budgetUsed?: number; latencyMs: number };
  /** Messages of a capture: sent, queued offline, refused, and held for the next capture. */
  capture?: { sent: number; queued: number; failed: number; held: number };
  /** The event committed the session. */
  committed?: boolean;
}

/** What an event's stage works with; `observe` gets the same. */
export interface EventContext {
  event: string;
  stage: Stage;
  read: EventRead;
  payload: Record<string, any>;
  cfg: Record<string, any>;
  cwd: string;
  /** The host's id for the session, and the OpenViking session it is captured into. */
  nativeSessionId: string;
  sessionId: string;
  fetchJSON: (path: string, init?: object, options?: object) => Promise<any>;
  log: (stage: string, data?: unknown) => void;
}

export interface Host {
  id: string;
  /** Prefix of the OpenViking session id a host session is captured into. */
  sessionPrefix: string;
  /** The host's event names, as its hook entries pass them, and what each means. */
  events: Record<string, Stage>;
  /** Milliseconds every request of one event shares. */
  budgets?: Record<string, number>;
  /** Write events may answer first and do their work in a detached process. */
  canDetach?: boolean;
  /** Write events that must finish inside the hook all the same. */
  syncEvents?: string[];
  /** Write events the host gives too little time: detached whatever `writePathAsync` says. */
  detachEvents?: string[];
  facts?: HostFacts;
  /** Replacement tools and example calls the URI guard names; the shared table when absent. */
  toolHints?: Record<string, { tool: unknown; example: unknown }>;
  read(event: string, payload: Record<string, any>, io: { text(ref: unknown): string | null }): EventRead;
  /** The transcript's path. Synchronous; may read the host's own index files. */
  locate?(ref: unknown): string | null;
  /** Pure. */
  decode(input: DecodeInput): Decoded;
  /** The value to print for the host, or null for nothing. */
  emit(event: string, output: Output, payload: Record<string, any>): unknown;
  /** What a tool-call payload is, or null for a call the plugin has no say in. */
  tool?(payload: Record<string, any>): ToolCall | null;
  /** The host's own session id out of an MCP tool call's `_meta`, where the host names it there. */
  mcpSession?(meta: Record<string, any>): string;
  /**
   * The cursor an earlier version of the plugin kept for this session outside
   * the session state, so an upgrade does not send again what it captured.
   * Synchronous; asked only until the flow has stored a cursor.
   */
  legacyCursor?(nativeSessionId: string, read: EventRead): unknown;
  /** The context as this host shows it, where it adds something of its own to what the flow built. */
  present?(ctx: EventContext, context: string): string;
  /** A local recall compressor run through the host's own CLI, or null when it cannot run. */
  compress?(cfg: Record<string, any>, log: EventContext["log"]): unknown;
  /**
   * Told after every event what it did, for files the host keeps for its own
   * interface (a statusline snapshot, a session binding). It cannot change the
   * answer, and a failure is only logged.
   */
  observe?(ctx: EventContext, outcome: Outcome): void | Promise<void>;
}
