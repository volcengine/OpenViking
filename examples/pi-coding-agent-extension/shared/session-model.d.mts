export function deriveHarnessSessionId(prefix: string, sessionId: string, suffix?: string): string;
export function deriveCodexSessionId(codexSessionId: string): string;
export function isBypassed(cfg: Record<string, any>, options?: { sessionId?: string; cwd?: string }): boolean;
