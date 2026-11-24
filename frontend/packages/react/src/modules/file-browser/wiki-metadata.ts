import {
  markdownComments,
  markdownRange,
  readMarkdownSource,
  walkMarkdown,
} from '../../shared/markdown/markdown-source.js';
import { startsCommentLine } from './fs-comment-trailers.js';

/** Some older generated files appended a trailer before closing a code span. */
function isLegacyTrailer(
  source: string,
  payload: string,
  marker: number,
  root: ReturnType<typeof readMarkdownSource>['root'],
) {
  let matchTexts: string[];
  try {
    const records = JSON.parse(`[${payload}]`) as unknown[];
    if (!records.length) return false;
    matchTexts = [];
    for (const value of records) {
      if (typeof value !== 'object' || !value) return false;
      const record = value as Record<string, unknown>;
      if (
        typeof record.match_text !== 'string' ||
        !record.match_text ||
        typeof record.problem_description !== 'string' ||
        (record.state !== 'fixed' && record.state !== 'unfixed')
      )
        return false;
      matchTexts.push(record.match_text);
    }
  } catch {
    return false;
  }

  let legacy = false;
  walkMarkdown(root, (node, quoteDepth) => {
    if (node.type !== 'code' && node.type !== 'inlineCode') return;
    const span = markdownRange(node);
    if (
      !span ||
      marker < span.start ||
      marker >= span.end ||
      !matchTexts.every((text) => source.slice(0, span.start).includes(text))
    )
      return;
    const fenced = /^(?:`{3,}|~{3,})/.test(source.slice(span.start));
    const inline = node.type === 'inlineCode';
    const indentedAfterText =
      !quoteDepth &&
      /^[ \t]*(?:\S[^\r\n]*)$/.test(
        source.slice(
          Math.max(
            source.lastIndexOf('\n', marker - 1),
            source.lastIndexOf('\r', marker - 1),
          ) + 1,
          marker,
        ),
      );
    legacy ||= inline || fenced || indentedAfterText;
  });
  return legacy;
}

/** Separate reserved Wiki trailers without requiring valid JSON in actual comments. */
export function splitWikiMetadata(content: string) {
  const pattern = /<!--\s*CONFLICTS_CONTENT(?![\p{L}\p{N}_])/gu;
  let document = content;
  const payloads: string[] = [];
  let complete = true;
  for (;;) {
    if (!document.includes('CONFLICTS_CONTENT')) break;
    const { bodyStart, source, root } = readMarkdownSource(document);
    const comments = new Set(
      markdownComments(source, root).map(({ start }) => start),
    );
    let trailer:
      { start: number; payload: string; closed: boolean } | undefined;
    for (const match of source.matchAll(pattern)) {
      const start = match.index;
      const afterMarker = start + match[0].length;
      const close = source.indexOf('-->', afterMarker);
      const closed = close >= 0;
      if (
        closed
          ? source.slice(close + 3).trim()
          : !startsCommentLine(source, start)
      )
        continue;
      const payload = source
        .slice(afterMarker, closed ? close : undefined)
        .trim();
      if (
        comments.has(start) ||
        (closed && isLegacyTrailer(source, payload, start, root))
      ) {
        trailer = { start, payload, closed };
        break;
      }
    }
    if (!trailer) break;
    payloads.unshift(trailer.payload);
    complete &&= trailer.closed;
    document = document.slice(0, bodyStart + trailer.start);
  }
  return { document, payloads, complete };
}
