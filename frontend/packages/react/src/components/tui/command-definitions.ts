import { matchTuiCommandInput } from './command-input.js';
import type {
  TuiCommandDefinition,
  TuiCommandGroup,
} from './command-catalog.js';

export interface TuiCommandSection {
  group: TuiCommandGroup;
  commands: readonly TuiCommandDefinition[];
}

/** 只匹配宿主目录，不包含内置业务命令或请求。 */
export function findTuiCommand(
  text: string,
  commands: readonly TuiCommandDefinition[],
) {
  return matchTuiCommandInput(text, commands)?.command;
}

export function filterTuiCommands(
  keyword: string,
  commands: readonly TuiCommandDefinition[],
) {
  const normalized = keyword.trim().toLowerCase();
  if (!normalized) return commands;
  return commands.filter(
    (command) =>
      command.name.toLowerCase().includes(normalized) ||
      (command.summary ?? command.description)
        .toLowerCase()
        .includes(normalized) ||
      command.aliases?.some((alias) =>
        alias.toLowerCase().includes(normalized),
      ),
  );
}

/** 保留宿主分组顺序，未声明的分组按首次出现顺序追加。 */
export function groupTuiCommands(
  commands: readonly TuiCommandDefinition[],
  groups: readonly TuiCommandGroup[] = [],
): readonly TuiCommandSection[] {
  const known = new Map(groups.map((group) => [group.key, group]));
  for (const command of commands) {
    const key = command.group ?? '';
    if (!known.has(key)) known.set(key, { key, label: key });
  }
  return Array.from(known.values(), (group) => ({
    group,
    commands: commands.filter((command) => (command.group ?? '') === group.key),
  })).filter((section) => section.commands.length > 0);
}
