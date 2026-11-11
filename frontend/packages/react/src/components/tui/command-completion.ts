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
  const content = normalize(value.trimStart().slice(slash ? 1 : 0));
  if (!slash && !content) return EMPTY_QUERY;
  const boundary = content.lastIndexOf(' ') + 1;
  const head = content.slice(0, boundary);
  const keyword = content.slice(boundary);
  const entries = commands.flatMap((command) =>
    (slash
      ? [command.name, ...(command.aliases ?? [])]
      : [getTuiCommandInputText(command)]
    ).map((text) => ({ command, text: normalize(text) })),
  );
  const definitions = (items: typeof entries) => [
    ...new Set(items.map((entry) => entry.command)),
  ];
  const scoped = entries.filter((entry) =>
    entry.text.toLowerCase().startsWith(head.toLowerCase()),
  );
  if (!scoped.length) return EMPTY_QUERY;
  const matching = scoped.filter((entry) =>
    entry.text.toLowerCase().startsWith(content.toLowerCase()),
  );
  // Command boundaries come from the longest match, including multiword slash names.
  const input = matchTuiCommandInput(value, commands);
  if (
    input &&
    (!slash || input.rest) &&
    !matching.some((entry) => entry.text.length > content.trimEnd().length)
  )
    return EMPTY_QUERY;
  const base = {
    ...EMPTY_QUERY,
    keyword,
    scope: slash ? '/' : head.trimEnd(),
    automatic: Boolean(slash),
  };
  if (slash)
    return {
      ...base,
      matches: !head
        ? filterTuiCommands(keyword, commands)
        : matching.length
          ? definitions(matching)
          : filterTuiCommands(keyword, definitions(scoped)),
    };
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
    const typed = content.slice(1);
    const name = [selected.name, ...(selected.aliases ?? [])]
      .map(normalize)
      .find((name) => name.toLowerCase().startsWith(typed.toLowerCase()));
    const suffix = name?.slice(typed.length);
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
