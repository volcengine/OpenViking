export const CONTEXT_BLOCK_MARKER: "<openviking-context";

export interface BudgetMessage {
  role?: string;
  content?: unknown;
  timestamp?: number;
  [key: string]: any;
}

export function flattenContent(msg: BudgetMessage): string;
export function fingerprintMessage(msg: BudgetMessage): string;
export function isUserTurnStart(msg: BudgetMessage): boolean;
export function countUserTurns(messages: BudgetMessage[]): number;
export function estimateTokens(text: string): number;
export function truncateToTokens(text: string, budget: number): string;
export function estimatePayloadTokens(payload: any): number;
