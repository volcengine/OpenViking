import type { SearchHit, SearchResult } from './search-types.js';

/**
 * 三组按顺序展平成一个列表。
 *
 * 语义检索按 URI 去重——同一个资源可能同时出现在两组里，搜索视图只需要「跳到哪个
 * 资源」，重复行只会让人以为有两个文件。
 *
 * grep 不去重：同一文件的多行命中是**不同的结果**，各带自己的行号与命中内容。
 * 按 URI 合并会把第 2 行之后的命中全部吞掉，用户会以为该文件只匹配了一次。
 *
 * Flattens the three groups into one list.
 *
 * Semantic hits deduplicate by URI: one resource can appear in two groups, and the view
 * only needs "which resource to jump to" — a duplicate row reads as two files.
 *
 * grep hits do not deduplicate: several matches in one file are **distinct results**,
 * each with its own line number and matched text. Merging by URI would swallow every
 * match after the first and make the file look like it matched once.
 */
export function flattenResult(result: SearchResult | null): SearchHit[] {
  if (!result) return [];
  const byUri = new Map<string, SearchHit>();
  const grepHits: SearchHit[] = [];
  for (const hit of [
    ...result.resources,
    ...result.memories,
    ...result.skills,
  ]) {
    if (hit.resultKind === 'grep') {
      grepHits.push(hit);
      continue;
    }
    const existing = byUri.get(hit.uri);
    // 同一个 URI 出现在两组里时保留有摘要的那条：摘要是行里唯一有信息量的
    // 副文本，丢掉它这一行就只剩文件名。
    //
    // When one URI appears in two groups, keep the entry that carries an abstract: the
    // abstract is the only informative secondary text in the row, and dropping it
    // leaves just the file name.
    if (!existing) byUri.set(hit.uri, hit);
    else {
      const best = hit.score > existing.score ? hit : existing;
      byUri.set(hit.uri, {
        ...best,
        abstract: best.abstract || hit.abstract || existing.abstract,
      });
    }
  }
  return [...byUri.values()].sort((a, b) => b.score - a.score).concat(grepHits);
}
