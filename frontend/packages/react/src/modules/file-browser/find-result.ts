import type { FsEntry } from './fs-model.js';

interface FindMatch {
  uri?: string;
  abstract?: string;
  score?: number;
}

/** Grouped matches from the semantic search endpoint. */
export interface FindResult {
  memories?: FindMatch[];
  resources?: FindMatch[];
  skills?: FindMatch[];
}

/** Rank across groups before deduplicating, preserving order for scoreless adapters. */
export function mergeFindResult(
  result: FindResult | null | undefined,
): FsEntry[] {
  const score = (match: FindMatch) =>
    typeof match.score === 'number' && Number.isFinite(match.score)
      ? match.score
      : -Infinity;
  const matches = [result?.memories, result?.resources, result?.skills]
    .flatMap((group) => group ?? [])
    .sort((left, right) => {
      const a = score(left),
        b = score(right);
      return a === b ? 0 : b > a ? 1 : -1;
    });
  const seen = new Set<string>();
  const entries: FsEntry[] = [];
  for (const match of matches) {
    if (!match.uri || seen.has(match.uri)) continue;
    seen.add(match.uri);
    entries.push({
      uri: match.uri,
      isDir: false,
      abstract: match.abstract?.trim() || undefined,
    });
  }
  return entries;
}
