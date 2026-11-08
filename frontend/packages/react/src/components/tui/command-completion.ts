import type { TuiCommandDefinition } from './command-catalog.js';
import { parseTuiCommandInput } from './command-catalog.js';
import { filterTuiCommands } from './command-definitions.js';
import {
  getTuiCommandInputText,
  matchTuiCommandInput,
} from './command-input.js';

export interface TuiCommandQuery {
  keyword: string;
  scope: string | null;
  matches: readonly TuiCommandDefinition[];
  automatic: boolean;
  canOpenPalette: boolean;
  prefixTarget?: string;
  colorInput: boolean;
}
export interface TuiInlineCompletion {
  kind: 'prefix' | 'command';
  suffix: string;
  value: string;
}
const EMPTY_QUERY: TuiCommandQuery = {
  keyword: '',
  scope: null,
  matches: [],
  automatic: false,
  canOpenPalette: false,
  colorInput: false,
};
const normalize = (text: string) => text.replace(/[ \t]+/g, ' ');

/** 只根据宿主的完整输入前缀推导命名空间和候选；参数阶段不继续补命令。 */
export function getTuiCommandQuery(
  value: string,
  commands: readonly TuiCommandDefinition[],
): TuiCommandQuery {
  if (!commands.length || /[\r\n]/.test(value)) return EMPTY_QUERY;
  const slash = parseTuiCommandInput(value);
  if (slash)
    return slash.hasTrailingSpace
      ? EMPTY_QUERY
      : {
          ...EMPTY_QUERY,
          keyword: slash.name,
          scope: '/',
          automatic: true,
          matches: filterTuiCommands(slash.name, commands),
        };
  const content = normalize(value.trimStart());
  if (!content || matchTuiCommandInput(value, commands)) return EMPTY_QUERY;
  const boundary = content.lastIndexOf(' ') + 1;
  const head = content.slice(0, boundary);
  const keyword = content.slice(boundary);
  const entries = commands.map((command) => ({
    command,
    text: normalize(getTuiCommandInputText(command)),
  }));
  const scoped = entries.filter((entry) =>
    entry.text.toLowerCase().startsWith(head.toLowerCase()),
  );
  if (!scoped.length) return EMPTY_QUERY;
  const matching = scoped.filter((entry) =>
    entry.text.toLowerCase().startsWith(content.toLowerCase()),
  );
  const base = { ...EMPTY_QUERY, keyword, scope: head.trimEnd() };
  if (!keyword)
    return {
      ...base,
      matches: matching.map((entry) => entry.command),
      canOpenPalette: matching.length > 0,
      colorInput: matching.length > 0,
    };
  const words = content.split(' ').length;
  if (
    matching.length &&
    matching.every((entry) => entry.text.split(' ').length > words)
  ) {
    const targets = [
      ...new Set(
        matching.map((entry) =>
          entry.text.split(' ').slice(0, words).join(' '),
        ),
      ),
    ];
    const target = targets.length === 1 ? targets[0] : undefined;
    return {
      ...base,
      matches: matching.map((entry) => entry.command),
      colorInput: true,
      prefixTarget: target,
      canOpenPalette: !target || target.toLowerCase() === content.toLowerCase(),
    };
  }
  return {
    ...base,
    matches: matching.length
      ? matching.map((entry) => entry.command)
      : filterTuiCommands(
          keyword,
          scoped.map((entry) => entry.command),
        ),
    colorInput: matching.length > 0,
  };
}

/** 提示永远不写入 value；只有显式接受时才使用返回的完整值。 */
export function getTuiInlineCompletion(
  value: string,
  query: TuiCommandQuery,
  selected: TuiCommandDefinition | undefined,
  paletteOpen = false,
): TuiInlineCompletion | undefined {
  const leading = value.slice(0, value.length - value.trimStart().length);
  const content = normalize(value.trimStart());
  if (query.prefixTarget) {
    const suffix = query.prefixTarget.slice(content.length);
    return suffix
      ? { kind: 'prefix', suffix, value: `${leading}${query.prefixTarget}` }
      : undefined;
  }
  if (!selected || (!query.keyword && (!paletteOpen || query.scope === '/')))
    return undefined;
  if (query.scope === '/') {
    const name = [selected.name, ...(selected.aliases ?? [])].find((name) =>
      name.toLowerCase().startsWith(query.keyword.toLowerCase()),
    );
    const suffix = name?.slice(query.keyword.length);
    return suffix
      ? {
          kind: 'command',
          suffix,
          value: `${leading}${getTuiCommandInputText(selected)} `,
        }
      : undefined;
  }
  const text = getTuiCommandInputText(selected);
  const normalized = normalize(text);
  if (!normalized.toLowerCase().startsWith(content.toLowerCase()))
    return undefined;
  const suffix = normalized.slice(content.length);
  return suffix
    ? { kind: 'command', suffix, value: `${leading}${text} ` }
    : undefined;
}
