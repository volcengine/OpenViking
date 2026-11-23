import { isQuotedComment, startsCommentLine } from './fs-comment-trailers.js';

/** Separate reserved Wiki trailers without requiring valid JSON. YAML stays in the body. */
export function splitWikiMetadata(content: string) {
  const pattern = /<!--\s*CONFLICTS_CONTENT(?![\p{L}\p{N}_])/gu;
  let document = content;
  const payloads: string[] = [];
  let complete = true;
  for (;;) {
    const matches = [...document.matchAll(pattern)];
    const trailer = matches.find((match) => {
      const close = document.indexOf('-->', match.index + match[0].length);
      return (
        (close < 0
          ? startsCommentLine(document, match.index)
          : !document.slice(close + 3).trim()) &&
        !isQuotedComment(document, match.index)
      );
    });
    if (!trailer) break;
    const close = document.indexOf('-->', trailer.index + trailer[0].length);
    payloads.unshift(
      document
        .slice(trailer.index + trailer[0].length, close < 0 ? undefined : close)
        .trim(),
    );
    complete &&= close >= 0;
    document = document.slice(0, trailer.index);
  }
  return { document, payloads, complete };
}
