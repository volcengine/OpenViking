import type {
  TuiRecordContent,
  TuiSearchResultItem,
} from '../../components/tui/index.js';

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
  values: Record<string, string | number | boolean>,
): TuiRecordContent {
  if (['read', 'abstract', 'overview'].includes(name))
    return {
      variant: 'resource-preview',
      title: String(values.uri).split('/').pop() || String(values.uri),
      uri: String(values.uri),
      content: text(data),
    };
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
            const label = key.replaceAll('_', ' ');
            return `${label[0]?.toUpperCase() ?? ''}${label.slice(1)}: ${text(value)}`;
          })
        : data === null || data === undefined
          ? []
          : [text(data)],
    };
  }
  if (['find', 'search'].includes(name)) {
    const items: TuiSearchResultItem[] = [];
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
        });
      }
    }
    return { variant: 'search-list', items };
  }
  if (
    ['stat', 'status', 'health', 'task status', 'task cancel'].includes(name) &&
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
