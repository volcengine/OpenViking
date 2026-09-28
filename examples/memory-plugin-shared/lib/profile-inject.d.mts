export function buildProfileBlock(
  fetchJSON: (path: string, init?: any, options?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  totalBudgetTokens: number,
  actorPeerId?: string,
  options?: { skillCatalog?: boolean; skillCatalogTokenBudget?: number; sessionStartMaxBytes?: number },
): Promise<null | {
  block: string;
  chars: number;
  tokens: number;
  profileUri: string;
  profileChars: number;
  prefCount: number;
  entCount: number;
  droppedPref: number;
  droppedEnt: number;
  skillCount: number;
  droppedSkill: number;
  skillTokens: number;
}>;

export function estimateTokens(text: string): number;

export function truncateToBytes(text: string, maxBytes: number): string;

export function isRepeatInjection(statePath: string, sessionId: string, block: string): boolean;
