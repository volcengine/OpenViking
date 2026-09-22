export function buildProfileBlock(
  fetchJSON: (path: string, init?: any, options?: any) => Promise<{ ok: boolean; status?: number; result?: any; error?: any }>,
  totalBudgetTokens: number,
  actorPeerId?: string,
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
}>;

export function estimateTokens(text: string): number;
