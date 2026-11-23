import { isRecord } from '../../client/response.js';
import type { CommandConsoleRecordContent } from '../../components/command-console/index.js';
import type { ConsoleResultNotice } from './command-messages.js';

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

export function formatListResult(
  name: string,
  data: unknown,
):
  | { content: CommandConsoleRecordContent; resultNotice?: ConsoleResultNotice }
  | undefined {
  if (name === 'task list' && Array.isArray(data)) {
    if (!data.length) {
      return {
        content: { variant: 'text', text: '' },
        resultNotice: 'noTasks',
      };
    }
    return {
      content: {
        variant: 'search-list',
        items: data.map((entry, index) => {
          const task = isRecord(entry) ? entry : {};
          return {
            id: String(index),
            title: typeof task.task_id === 'string' ? task.task_id : '',
            typeLabel:
              typeof task.status === 'string' ? task.status : undefined,
            content: [task.task_type, task.updated_at_iso, task.error]
              .filter((value) => typeof value === 'string' && value.length > 0)
              .join('\n'),
          };
        }),
      },
    };
  }
  if (name !== 'grep' || !isRecord(data) || !Array.isArray(data.matches))
    return undefined;
  return {
    resultNotice:
      data.limit_reached === true || data.candidate_limit_reached === true
        ? 'grepLimitReached'
        : undefined,
    content: {
      variant: 'search-list',
      items: data.matches.map((entry, index) => {
        const match = isRecord(entry) ? entry : {};
        const uri = typeof match.uri === 'string' ? match.uri : undefined;
        const tags: unknown[] = Array.isArray(match.tags) ? match.tags : [];
        return {
          id: String(index),
          title: uri ?? '',
          uri,
          levelLabel:
            typeof match.line === 'number' ? `:${match.line}` : undefined,
          content: grepExcerpt(match),
          tags: tags.filter((tag) => typeof tag === 'string'),
        };
      }),
    },
  };
}
