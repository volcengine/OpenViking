import type { FsEntry } from './fs-model.js';

/** 语义搜索返回的分组结果。Grouped results of a semantic search. */
export interface FindResult {
  memories?: Array<{ uri?: string; abstract?: string }>;
  resources?: Array<{ uri?: string; abstract?: string }>;
  skills?: Array<{ uri?: string; abstract?: string }>;
}

/**
 * 把分组结果合并成一个按 URI 去重的列表。搜索视图只需要「跳到哪个资源」，类别
 * 分组对使用者没有意义。
 *
 * Merges the grouped result into one list deduplicated by URI: the search view
 * only needs "which resource to jump to", so the category split adds nothing.
 */
export function mergeFindResult(
  result: FindResult | null | undefined,
): FsEntry[] {
  const seen = new Set<string>();
  const entries: FsEntry[] = [];

  for (const group of [result?.memories, result?.resources, result?.skills]) {
    for (const item of group ?? []) {
      const uri = item?.uri;
      if (!uri || seen.has(uri)) continue;
      seen.add(uri);
      entries.push({
        uri,
        isDir: false,
        abstract: item.abstract?.trim() || undefined,
      });
    }
  }
  return entries;
}
