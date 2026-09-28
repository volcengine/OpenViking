export const WINDOW_ENTRY_TYPE: "ov-context-window";
export const WINDOW_HEADER_OPEN: '<openviking-context source="context-window">';
export const WINDOW_HEADER_CLOSE: "</openviking-context>";
export const STATUS_CUSTOM_TYPE: "ov-context-status";
export const REMINDER_CUSTOM_TYPE: "ov-context-reminder";
export const HANDOFF_MARKER: "[Context Window Handoff]";
export const STATUS_MARKER: "[context-status]";
export const REMINDER_MARKER: "[context-reminder]";
export const COMPACTION_SENTINEL: "ov-context-window-reset";

export interface WindowMessage {
  role: string;
  content?: unknown;
  timestamp?: number;
  toolCallId?: string;
  toolName?: string;
  customType?: string;
  [key: string]: any;
}

export interface WindowConfig {
  resetDeadlineMs: number;
  archivePollMs: number;
  overviewRefreshMaxAttempts: number;
  overviewBudget: number;
  notesBudget: number;
  pendingRequestBudget: number;
  softPercent: number;
  hardPercent: number;
  idleGapMinutes: number;
  statusEveryTurn: boolean;
  historyItemMaxChars: number;
  recentResetGuardMs: number;
}

export function windowConfig(raw?: Partial<WindowConfig> & Record<string, any>): WindowConfig;

export interface CutRange {
  assistantIndex: number;
  resultIndex: number;
  cutEndIndex: number;
  droppedToolResults: number;
  siblingToolNames: string[];
}

export function computeCutRange(messages: WindowMessage[], anchorToolCallId: string): CutRange | null;

export function applyWindowCut(
  messages: WindowMessage[],
  opts: { anchorToolCallId: string; headerText: string; headerTimestamp?: number },
): { messages: WindowMessage[]; applied: boolean; droppedCount: number };

export function buildHandoffMessage(opts: {
  windowId: string;
  nextWindowId: string;
  reason?: string;
  notes?: string;
  nextSteps?: string[];
}): string;

export type OverviewState = "ready" | "pending" | "unavailable";

export function buildWindowHeader(opts: {
  windowId: string;
  previousWindowId?: string;
  archiveId?: string;
  archiveUri?: string;
  openedAt?: number;
  reason?: string;
  notes?: string;
  nextSteps?: string[];
  pendingRequest?: string;
  overview?: string;
  overviewState?: OverviewState;
  previousOverview?: string;
  /** Archive the stale Working Memory block actually belongs to. */
  previousArchiveId?: string;
  /** Messages OpenViking rejected for good; named in the header when > 0. */
  undeliveredCount?: number;
  siblingToolNames?: string[];
  config?: Partial<WindowConfig> & Record<string, any>;
}): string;

export function buildStatusLine(opts: {
  windowId: string;
  turnsInWindow?: number;
  usedTokens?: number;
  contextWindow?: number;
  estimated?: boolean;
  sinceLastUserMs?: number | null;
  /**
   * Gap the user just came back from. This is what gates the idle NOTE;
   * `idleGapMs` is only a fallback for a caller that has nothing else.
   */
  idleGapMs?: number | null;
  idleGapMinutes?: number;
}): string;

export function reminderText(
  level: "soft" | "hard",
  opts: { windowId: string; usedTokens?: number; contextWindow?: number },
): string;

/**
 * Token estimate for one pi message, including toolCall arguments and thinking
 * blocks (which carry no `text` field and would otherwise score as zero).
 */
export function messageTokens(msg: WindowMessage): number;

/**
 * Clamp the soft/hard reminder thresholds under pi's own auto-compaction line
 * (`contextTokens > contextWindow - reserveTokens`). Pass `reserveTokens: 0`
 * (or omit it) to leave the configured thresholds untouched.
 */
export function effectiveThresholds(opts?: {
  softPercent?: number;
  hardPercent?: number;
  contextWindow?: number;
  reserveTokens?: number;
}): { softPercent: number; hardPercent: number };

export function formatTokens(n: number): string;
export function formatDuration(ms: number): string;
export function lastUserTimestamps(messages: WindowMessage[]): number[];
export function lastUserTextFromBranch(branch: any[]): string;
/**
 * Branch entries → the message list `buildSessionContext()` would produce
 * (messages, custom messages, branch summaries, compaction handling). Used
 * where no `context` hook has run yet in this process.
 */
export function messagesFromBranch(entries: any[]): WindowMessage[];
export function siblingToolNamesFromBranch(branch: any[], anchorToolCallId: string): string[];

export interface ArchiveRef {
  windowId: string;
  archiveId: string;
  archiveUri: string;
}

export interface WindowPersistedState {
  version: number;
  ovSessionId: string | null;
  windowIndex: number;
  anchorToolCallId: string | null;
  openedAt: number;
  headerText: string;
  reason: string;
  notes: string;
  nextSteps: string[];
  pendingRequest: string;
  archiveId: string;
  archiveUri: string;
  taskId: string;
  /** Every archive of this session, oldest first (bounded to the last 100). */
  archives: ArchiveRef[];
  /** Archive of the window before the current one. */
  previousArchiveId: string;
  /** Messages OpenViking rejected for good; they are missing from the archive. */
  undeliveredCount: number;
  overviewReady: boolean;
  /** The archive will never get a Working Memory (task failed / budget spent). */
  overviewUnavailable: boolean;
  overviewAttempts: number;
  /** Working Memory of the *previous* window, truncated to `overviewBudget`. */
  previousOverview: string;
  siblingToolNames: string[];
  syncedEntryCount: number;
  lastResetAt: number;
  lastResetBy: string;
}

export interface WindowCommitResult {
  status?: string;
  reason?: string;
  task_id?: string;
  archive_uri?: string;
  trace_id?: string;
  [key: string]: any;
}

export interface ContextWindowIo {
  syncBranch?: (branch: any[]) => Promise<{ added: number; tokens: number; allDelivered: boolean }> | { added: number; tokens: number; allDelivered: boolean };
  flush?: (opts: { budgetMs: number }) => Promise<boolean> | boolean;
  postHandoff?: (text: string) => Promise<boolean> | boolean;
  commit?: (opts: { keepRecentCount: number }) => Promise<WindowCommitResult | null> | WindowCommitResult | null;
  readArchiveOverview?: (archiveUri: string) => Promise<string | null> | string | null;
  getTask?: (taskId: string) => Promise<{ status?: string } | null> | { status?: string } | null;
  persistEntry?: (customType: string, data: WindowPersistedState) => void;
  getWatermark?: () => number;
  now?: () => number;
  sleep?: (ms: number, signal?: AbortSignal | null) => Promise<void>;
  log?: (message: string) => void;
  connected?: () => boolean;
  sessionId?: () => string | null;
  pendingCount?: () => number;
  /** Messages this session lost for good (non-retryable rejections, dropped queue entries). */
  droppedCount?: () => number;
  /** trace_id of the last commit response, reported in a commit refusal. */
  commitTraceId?: () => string;
}

/**
 * A commit that threw after the request was sent: the archive may or may not
 * exist, so the next reset re-checks connectivity before it syncs again.
 */
export interface CommitTransportError {
  at: number;
  message: string;
}

export interface ResetOutcome {
  ok: boolean;
  kind: "reset" | "refused" | "noop";
  text: string;
  details: Record<string, any>;
}

export interface StatusSnapshot {
  windowId: string;
  turnsInWindow: number;
  usedTokens: number;
  contextWindow: number;
  percent: number;
  tokensLeft: number | null;
  estimated: boolean;
  sinceLastUserMs: number | null;
  idleGapMs: number | null;
  archiveId: string;
  /** Archives this session produced (not derived from the window index). */
  archiveCount: number;
  archives: ArchiveRef[];
  undeliveredCount: number;
  overviewReady: boolean;
  windowAgeMs: number | null;
  /** Effective thresholds for this snapshot (clamped when reserveTokens is known). */
  softPercent: number;
  hardPercent: number;
  advice: string;
}

export interface BeforeCompactResult {
  compaction: {
    summary: string;
    firstKeptEntryId: any;
    tokensBefore: number;
    details: { source: string; reason: string };
  };
}

export class ContextWindowCore {
  constructor(opts?: { config?: Partial<WindowConfig> & Record<string, any>; io?: ContextWindowIo });
  config: WindowConfig;
  get windowId(): string;
  get armed(): boolean;
  get state(): WindowPersistedState & {
    windowId: string;
    armed: boolean;
    resetting: boolean;
    remindersSent: { soft: boolean; hard: boolean };
    awaitingFirstObservation: boolean;
    lastWindowTokens: number;
    turnsInWindow: number;
    lastCommitError: CommitTransportError | null;
  };
  /** True while a restore is still waiting for the OpenViking session id. */
  pendingSessionCheck: boolean;
  /** Archives of this session, oldest first, with the window each belongs to. */
  archives: ArchiveRef[];
  lastCommitError: CommitTransportError | null;
  persistedState(): WindowPersistedState;
  restore(entries: any[]): this["state"];
  /**
   * Settle a restore that ran before `io.sessionId()` was known. Returns false
   * (and drops the window) when the entry belongs to another session. Idempotent.
   */
  validateSession(): boolean;
  persist(): void;
  shutdown(): Promise<void>;
  transformContext(messages: WindowMessage[]): WindowMessage[];
  /**
   * `transformContext` without the right to release the boundary: the cut and
   * the window metrics for a caller that only describes the window (the status
   * line, built from the session branch before any `context` hook has run).
   */
  observeMessages(messages: WindowMessage[]): WindowMessage[];
  /**
   * Refresh `lastWindowTokens` / `turnsInWindow` from a message list.
   * With `headerIndex < 0` only assistant messages newer than the last reset count.
   */
  recordWindowMetrics(messages: WindowMessage[], headerIndex: number): void;
  observeAssistantResponse(): void;
  /** Release the boundary and clear the window metrics. */
  disarm(reason: string): void;
  /** Flush budget for one barrier: clamped to [2s, 15s] of the remaining deadline. */
  flushBudget(deadline: number): number;
  requestReset(opts: {
    reason?: string;
    notes?: string;
    nextSteps?: string[];
    toolCallId: string;
    branch?: any[];
    signal?: AbortSignal | null;
    onProgress?: (message: string) => void;
    siblingToolNames?: string[];
  }): Promise<ResetOutcome>;
  refreshPendingOverview(): Promise<boolean>;
  markOverviewUnavailable(): void;
  /** Remember which window an archive belongs to. Idempotent per archive id. */
  recordArchive(windowId: string, archiveId: string, archiveUri?: string): void;
  /** Post a one-line retraction for a handoff note whose reset then failed. */
  retractHandoff(): Promise<void>;
  rebuildHeader(opts: { overviewState?: OverviewState; previousWindowId?: string }): string;
  /** Reported usage when it is trustworthy, the local estimate otherwise. */
  usedTokens(usage?: { tokens?: number | null } | null): { used: number; estimated: boolean };
  /** Effective soft/hard percentages for a given window size and pi reserve. */
  thresholdsFor(contextWindow: number, reserveTokens?: number): { softPercent: number; hardPercent: number };
  dueReminder(opts?: {
    usage?: { tokens?: number | null; contextWindow?: number } | null;
    contextWindow?: number;
    /** pi settings `compaction.reserveTokens` (default 16384); 0 disables the clamp. */
    reserveTokens?: number;
  }): "soft" | "hard" | null;
  statusSnapshot(opts?: {
    usage?: { tokens?: number | null; contextWindow?: number } | null;
    now?: number | null;
    messages?: WindowMessage[];
    reserveTokens?: number;
  }): StatusSnapshot;
  handleBeforeCompact(opts: {
    preparation?: { firstKeptEntryId?: any; tokensBefore?: number };
    branchEntries?: any[];
  }): Promise<BeforeCompactResult | undefined>;
  pickFirstKeptEntryId(preparation?: { firstKeptEntryId?: any }, branchEntries?: any[]): any;
  absorbExternalCompaction(reason?: string): void;
}
