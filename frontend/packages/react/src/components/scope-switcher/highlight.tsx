import type { ReactNode } from 'react';

/**
 * 把标签里所有命中关键字的片段包进 `<mark>`，逐处高亮而不是只标第一处。大小写不敏感，保留原文大小写。
 *
 * Wraps every keyword hit in the label with `<mark>`, highlighting each occurrence
 * rather than only the first. Case-insensitive; the original casing is preserved.
 */
export function Highlight({
  text,
  keyword,
}: {
  text: string;
  keyword: string;
}) {
  const needle = keyword.trim().toLocaleLowerCase();
  if (!needle) return <>{text}</>;
  const haystack = text.toLocaleLowerCase();
  const parts: ReactNode[] = [];
  let cursor = 0;
  let hit = haystack.indexOf(needle);
  while (hit >= 0) {
    if (hit > cursor) parts.push(text.slice(cursor, hit));
    const end = hit + needle.length;
    parts.push(<mark key={`${hit}-${end}`}>{text.slice(hit, end)}</mark>);
    cursor = end;
    hit = haystack.indexOf(needle, cursor);
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return <>{parts}</>;
}
