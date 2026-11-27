export function collectToolNamesByIdFromEntries(entries: any[]): Record<string, string>;
export function findLastHumanTurnIndex(turns: any[]): number;
export function extractPartsFromPayload(payload: any, options?: Record<string, any>): any[];
export function extractTextFromPayload(payload: any, options?: Record<string, any>): string;
export function shapeCaptureParts(parts: any[], role: string, cfg?: Record<string, any>): { parts: any[]; dropped: boolean };
export function buildCaptureMessage(payload: any, role: string, cfg?: Record<string, any>, options?: {
  toolNameById?: Record<string, string>;
}): { role: string; parts: any[] } | null;
export function hasCaptureTrigger(text: string): boolean;
export function extractCaptureTurns(rolloutEntries: any[], cfg?: Record<string, any>): Array<{
  role: string;
  text: string;
  parts: any[];
}>;
export function sanitizeCapturedText(text: string, options?: { role?: string }): string;

export function isCaptureEnabled(cfg?: Record<string, any>): boolean;
