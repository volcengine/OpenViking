export function collectToolNamesByIdFromEntries(entries: any[]): Record<string, string>;
export function findLastHumanTurnIndex(turns: any[]): number;
export function extractPartsFromPayload(payload: any, options?: Record<string, any>): any[];
export function extractTextFromPayload(payload: any, options?: Record<string, any>): string;
export function shapeCaptureParts(parts: any[], role: string, cfg?: Record<string, any>): { parts: any[]; dropped: boolean };
export function shapeCapturePayload(payload: any, role: string, cfg?: Record<string, any>, options?: {
  toolNameById?: Record<string, string>;
  faithful?: boolean;
}): { parts: any[]; text: string; signalText: string; dropped: boolean };
export function shouldCaptureText(text: string, role: string, cfg?: Record<string, any>, options?: { filters?: boolean }): {
  shouldCapture: boolean;
  reason: string;
  text: string;
};
export function sanitizeCapturedText(text: string): string;
export function truncateCaptureText(text: string, maxChars?: number): string;

export function isCaptureEnabled(cfg?: Record<string, any>): boolean;
