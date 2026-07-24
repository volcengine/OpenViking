export const TAKEOVER_ENTRY_TYPE: "ov-takeover";
export const OVERVIEW_MARKER: "[OpenViking Session Context]";

export interface TakeoverMessage {
  role: string;
  content?: unknown;
  timestamp?: number;
  [key: string]: any;
}

export interface TakeoverPersistedState {
  coveredUserTurns: number;
  overview: string;
  fingerprint?: string | null;
  pendingTokens: number;
  lastSeenUserTurns?: number;
  syncedEntryCount?: number;
}

export interface TakeoverConfig {
  takeoverEnabled?: boolean;
  takeoverTokenThreshold?: number;
  takeoverKeepRecentTurns?: number;
  takeoverOverviewBudget?: number;
  takeoverOverviewPollMs?: number;
  takeoverOverviewPollMax?: number;
}

export interface TakeoverIo {
  flush?: () => Promise<boolean> | boolean;
  commit?: (opts?: { queueOnFailure?: boolean; keepRecentCount?: number }) => Promise<unknown> | unknown;
  fetchOverview?: (tokenBudget?: number) => Promise<string | { latest_archive_overview?: string | null } | null> | string | { latest_archive_overview?: string | null } | null;
  persistEntry?: (customType: string, data: TakeoverPersistedState) => void;
  getWatermark?: () => number;
  sleep?: (ms: number) => Promise<void>;
  log?: (message: string) => void;
}

export function flattenContent(msg: TakeoverMessage): string;
export function fingerprintMessage(msg: TakeoverMessage): string;
export function isUserTurnStart(msg: TakeoverMessage): boolean;
export function countUserTurns(messages: TakeoverMessage[]): number;
export function findBoundaryIndex(messages: TakeoverMessage[], coveredUserTurns: number): number;
export function estimateTokens(text: string): number;
export function truncateToTokens(text: string, budget: number): string;
export function estimatePayloadTokens(payload: any): number;
export function buildOverviewMessage(overview: string, firstKeptTs?: number, budget?: number): TakeoverMessage;
export function countUndeliveredForSession(pendingEntries: any[], sid: string): number;

export class TakeoverCore {
  constructor(opts?: { config?: TakeoverConfig; io?: TakeoverIo });
  get enabled(): boolean;
  get state(): TakeoverPersistedState & {
    fingerprint: string | null;
    lastSeenUserTurns: number;
    syncedEntryCount: number;
    committing: boolean;
  };
  restore(entries: any[]): this["state"];
  transformContext(messages: TakeoverMessage[]): TakeoverMessage[];
  noteSynced(estTokens: number): void;
  commitIfDue(): Promise<boolean>;
  onTurnSynced(estTokens: number): Promise<boolean>;
  commitAndAdvance(): Promise<boolean>;
  handleBeforeCompact(preparation?: { firstKeptEntryId?: string; tokensBefore?: number }): Promise<
    | {
        compaction: {
          summary: string;
          firstKeptEntryId: string;
          tokensBefore: number;
          details: { source: string };
        };
      }
    | undefined
  >;
  shutdown(): Promise<void>;
  resetBoundary(reason?: string): void;
  truncatedOverview(): string;
  persistedState(): TakeoverPersistedState;
  persist(): void;
  pollOverview(): Promise<string>;
}
