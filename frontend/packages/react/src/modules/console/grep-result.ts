import { isRecord } from '../../client/response.js';
import type { CommandConsoleSearchListContent } from '../../components/command-console/index.js';
import type { Locale } from '../../shared/locale.js';
import { resolveConsoleLocale } from './labels.js';

function grepExcerpt(match: Record<string, unknown>): string | undefined {
  const before: unknown[] = Array.isArray(match.before_context)
    ? match.before_context
    : [];
  const after: unknown[] = Array.isArray(match.after_context)
    ? match.after_context
    : [];
  if (!before.length && !after.length) {
    return typeof match.content === 'string' ? match.content : undefined;
  }
  return [...before, match, ...after]
    .flatMap((entry) => {
      if (!isRecord(entry)) return [];
      const { line, content } = entry;
      return typeof line === 'number' && typeof content === 'string'
        ? [`${line}: ${content}`]
        : [];
    })
    .join('\n');
}

export function formatGrepResult(
  result: Record<string, unknown>,
  locale: Locale,
): CommandConsoleSearchListContent | undefined {
  if (!Array.isArray(result.matches)) return undefined;
  return {
    variant: 'search-list',
    summary:
      result.limit_reached === true || result.candidate_limit_reached === true
        ? resolveConsoleLocale(locale).grepLimitReached
        : undefined,
    items: result.matches.map((entry, index) => {
      const match = isRecord(entry) ? entry : {};
      const uri = typeof match.uri === 'string' ? match.uri : undefined;
      return {
        id: String(index),
        title: uri ?? '',
        uri,
        levelLabel:
          typeof match.line === 'number' ? `:${match.line}` : undefined,
        content: grepExcerpt(match),
        metaLabel: Array.isArray(match.tags)
          ? match.tags.join(', ')
          : undefined,
      };
    }),
  };
}
