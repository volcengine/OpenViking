import type { CommandConsoleCommandDefinition } from './command-catalog.js';

export interface CommandConsoleCommandInputMatch {
  command: CommandConsoleCommandDefinition;
  leadingWhitespace: string;
  prefix: string;
  rest: string;
}

/** 填入的真实命令前缀由宿主提供，不在组件中绑定某个 CLI。 */
export function getCommandConsoleCommandInputText(
  command: CommandConsoleCommandDefinition,
): string {
  return command.inputText?.trim() || command.name;
}

export function matchCommandConsoleCommandInput(
  value: string,
  commands: readonly CommandConsoleCommandDefinition[],
): CommandConsoleCommandInputMatch | undefined {
  const content = value.trimStart();
  const leadingWhitespace = value.slice(0, value.length - content.length);
  const matches = commands.flatMap((command) => {
    const names = [command.name, ...(command.aliases ?? [])];
    return [
      getCommandConsoleCommandInputText(command),
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
export function formatCommandConsoleCommandInput(
  value: string,
  commands: readonly CommandConsoleCommandDefinition[],
): string {
  const match = matchCommandConsoleCommandInput(value, commands);
  return match
    ? `${match.leadingWhitespace}${getCommandConsoleCommandInputText(match.command)}${match.rest}`
    : value;
}

export function getCommandConsoleArgumentHints(
  command: CommandConsoleCommandDefinition,
) {
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
