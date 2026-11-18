import { readTaskReferences } from '../../shared/task-tracking/read-references.js';
import type {
  CommandConsoleRecordContent,
  CommandConsoleSearchResultItem,
} from '../../components/command-console/index.js';
import type { ArgumentValues } from './command-parser.js';

function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}
function text(value: unknown): string {
  return typeof value === 'string'
    ? value
    : (JSON.stringify(value, null, 2) ?? '');
}

export function formatResult(
  name: string,
  data: unknown,
  values: ArgumentValues,
): CommandConsoleRecordContent {
  if (['read', 'abstract', 'overview'].includes(name)) {
    const uri = values.uri as string;
    return {
      variant: 'resource-preview',
      title: uri.split('/').pop() || uri,
      uri,
      content: text(data),
    };
  }
  if (['ls', 'tree'].includes(name) && Array.isArray(data))
    return {
      variant: 'resource-tree',
      items: data.map((entry, index) => {
        const item = object(entry);
        const uri = typeof item.uri === 'string' ? item.uri : undefined;
        return {
          id: `${index}`,
          title:
            typeof entry === 'string'
              ? entry
              : text(item.name ?? item.rel_path ?? uri ?? index),
          uri,
          metaLabel:
            typeof item.abstract === 'string' ? item.abstract : undefined,
        };
      }),
    };
  const result = object(data);
  if (
    [
      'add-resource',
      'add-skill',
      'add-memory',
      'compile',
      'compile create',
      'mkdir',
      'cp',
      'mv',
      'rm',
      'write',
      'session new',
      'session delete',
      'session add-message',
      'session add-messages',
      'session commit',
    ].includes(name)
  ) {
    const hidden =
      name === 'add-resource'
        ? new Set(['sourcepath', 'tempuri'])
        : new Set<string>();
    const rows = Object.entries(result).filter(
      ([key, value]) =>
        value !== undefined &&
        value !== null &&
        !hidden.has(key.replace(/_/g, '').toLowerCase()),
    );
    return {
      variant: 'write-result',
      lines: rows.length
        ? rows.slice(0, 12).map(([key, value]) => {
            const label = (
              key === 'status' && readTaskReferences(name, data).length
                ? 'submission_status'
                : key
            ).replaceAll('_', ' ');
            const title = `${label[0]?.toUpperCase() ?? ''}${label.slice(1)}`;
            if (
              /(?:^|_)uri$/i.test(key) &&
              typeof value === 'string' &&
              /^viking:\/\//i.test(value)
            ) {
              return { label: title, uri: value };
            }
            return `${title}: ${text(value)}`;
          })
        : data === null || data === undefined
          ? []
          : [text(data)],
    };
  }
  if (name === 'search' && values.mode === 'context')
    return {
      variant: 'text',
      text:
        typeof result.rendered === 'string' && result.rendered.trim()
          ? result.rendered
          : text(data),
    };
  if (['find', 'search'].includes(name)) {
    const items: CommandConsoleSearchResultItem[] = [];
    for (const key of ['memories', 'resources', 'skills']) {
      const entries = result[key];
      if (!Array.isArray(entries)) continue;
      for (const entry of entries) {
        const item = object(entry);
        const uri = typeof item.uri === 'string' ? item.uri : undefined;
        items.push({
          id: `${key}-${items.length}`,
          title: uri?.split('/').pop() || uri || key,
          uri,
          metaLabel: uri,
          typeLabel:
            typeof item.context_type === 'string' ? item.context_type : key,
          levelLabel:
            typeof item.level === 'number' ? `L${item.level}` : undefined,
          scoreLabel:
            typeof item.score === 'number' ? item.score.toFixed(3) : undefined,
          preview:
            typeof item.abstract === 'string' ? item.abstract : undefined,
          content:
            values.read_content === true && typeof item.content === 'string'
              ? item.content
              : undefined,
        });
      }
    }
    return { variant: 'search-list', items };
  }
  if (
    [
      'stat',
      'status',
      'health',
      'task status',
      'task cancel',
      'session get',
      'session get-session-context',
      'session get-session-archive',
    ].includes(name) &&
    Object.keys(result).length
  )
    return {
      variant: 'status',
      rows: Object.entries(result).map(([label, value]) => ({
        label,
        value: text(value),
        variant: typeof value === 'object' ? 'pre' : 'inline',
      })),
    };
  return { variant: 'text', text: text(data) };
}
