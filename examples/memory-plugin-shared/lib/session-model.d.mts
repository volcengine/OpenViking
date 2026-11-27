export function deriveHarnessSessionId(prefix: string, sessionId: string, suffix?: string): string;
export function deriveCodexSessionId(codexSessionId: string): string;
export function isBypassed(cfg: Record<string, any>, options?: { sessionId?: string; cwd?: string }): boolean;
export function thresholdCommitDue(
  fetchJSON: (path: string, init?: any) => Promise<unknown>,
  sessionId: string,
  cfg: Record<string, any>,
  meta?: { pending_tokens?: number; auto_commit_policy?: Record<string, number> | null } | null,
): Promise<boolean>;
export function commitsOnCompaction(cfg: Record<string, any>): boolean;
