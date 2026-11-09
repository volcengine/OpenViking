import type { TuiCommandDefinition } from './command-catalog.js';

export interface TuiCommandInputMatch {
  command: TuiCommandDefinition;
  leadingWhitespace: string;
  prefix: string;
  rest: string;
}

/** 填入的真实命令前缀由宿主提供，不在组件中绑定某个 CLI。 */
export function getTuiCommandInputText(command: TuiCommandDefinition): string {
  return command.inputText?.trim() || command.name;
}

export function matchTuiCommandInput(
  value: string,
  commands: readonly TuiCommandDefinition[],
): TuiCommandInputMatch | undefined {
  const content = value.trimStart();
  const leadingWhitespace = value.slice(0, value.length - content.length);
  const matches = commands.flatMap((command) => {
    const names = [command.name, ...(command.aliases ?? [])];
    return [
      getTuiCommandInputText(command),
      ...names,
      ...names.map((name) => `/${name}`),
    ].flatMap((prefix) => {
      const words = prefix.trim().split(/[ \t]+/);
      const pattern = words
        .map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
        .join('[ \t]+');
      const match =
        pattern && new RegExp(`^${pattern}(?=$|\\s)`, 'i').exec(content);
      return match
        ? [
            {
              command,
              leadingWhitespace,
              prefix: match[0],
              rest: content.slice(match[0].length),
            },
          ]
        : [];
    });
  });
  return matches.sort(
    (left, right) => right.prefix.length - left.prefix.length,
  )[0];
}

/** 仅用于接受候选、示例或历史；不改写用户正在编辑/组合的文本。 */
export function formatTuiCommandInput(
  value: string,
  commands: readonly TuiCommandDefinition[],
): string {
  const match = matchTuiCommandInput(value, commands);
  return match
    ? `${match.leadingWhitespace}${getTuiCommandInputText(match.command)}${match.rest}`
    : value;
}

export function getTuiArgumentHints(command: TuiCommandDefinition) {
  const params = command.params ?? [];
  const positional = params.filter((param) => !param.label.startsWith('-'));
  if (positional.length) return positional;
  return [
    ...params.filter((param) => param.required),
    ...(command.options ?? [])
      .filter((option) => option.required)
      .map((option) => ({
        label: option.token,
        description: option.description,
      })),
  ];
}
