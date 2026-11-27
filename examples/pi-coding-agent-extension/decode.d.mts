import type { Decoded, DecodeInput, Turn } from "./shared/flow/host.d.mts";

export function branchTurns(branch: any[]): Turn[];
export function decode(input: DecodeInput): Decoded;
export function captureCount(branchSlice: any[], cfg?: Record<string, any>): number;
export function branchCursor(branch: any[], ovSessionId: string, forked?: boolean): { lastEntryId: string } | undefined;
