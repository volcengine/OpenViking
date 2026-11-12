import { splitLines } from './fs-lines.js';

export type DiffLine = { kind: 'same' | 'added' | 'removed'; text: string };

/** Preserve exact line endings and bound the work for large or unrelated files. */
export function diffLines(before: string, after: string): DiffLine[] {
  const old = splitLines(before);
  const next = splitLines(after);
  let start = 0;
  while (
    start < old.length &&
    start < next.length &&
    old[start] === next[start]
  )
    start++;
  let end = 0;
  while (
    end < old.length - start &&
    end < next.length - start &&
    old[old.length - 1 - end] === next[next.length - 1 - end]
  )
    end++;
  const a = old.slice(start, old.length - end);
  const b = next.slice(start, next.length - end);
  const lines: DiffLine[] = old
    .slice(0, start)
    .map((text) => ({ kind: 'same', text }));
  const stride = b.length + 1;
  if (a.length && b.length && (a.length + 1) * stride <= 1_000_000) {
    const lcs = new Uint32Array((a.length + 1) * stride);
    for (let i = a.length - 1; i >= 0; i--) {
      for (let j = b.length - 1; j >= 0; j--) {
        const index = i * stride + j;
        lcs[index] =
          a[i] === b[j]
            ? lcs[index + stride + 1]! + 1
            : Math.max(lcs[index + 1]!, lcs[index + stride]!);
      }
    }
    let i = 0;
    let j = 0;
    while (i < a.length || j < b.length) {
      if (i < a.length && j < b.length && a[i] === b[j]) {
        lines.push({ kind: 'same', text: a[i++]! });
        j++;
      } else if (
        i < a.length &&
        (j === b.length ||
          lcs[(i + 1) * stride + j]! >= lcs[i * stride + j + 1]!)
      ) {
        lines.push({ kind: 'removed', text: a[i++]! });
      } else {
        lines.push({ kind: 'added', text: b[j++]! });
      }
    }
  } else {
    for (const text of a) lines.push({ kind: 'removed', text });
    for (const text of b) lines.push({ kind: 'added', text });
  }
  for (let i = old.length - end; i < old.length; i++)
    lines.push({ kind: 'same', text: old[i]! });
  return lines;
}
