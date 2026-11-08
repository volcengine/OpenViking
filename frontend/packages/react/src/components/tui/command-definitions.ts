import {
  TUI_COMMAND_GROUPS,
  type TuiCommandDefinition,
  type TuiCommandGroup,
} from './command-catalog.js';
import { CORE_COMMANDS } from './command-core.js';
import { FILESYSTEM_COMMANDS } from './command-filesystem.js';
import { SESSION_COMMANDS } from './command-session.js';

/**
 * 与 `ov` CLI 对齐的命令定义。CLI 新增命令时在对应分组文件补充，组件据此
 * 生成补全、参数提示与帮助。
 *
 * Command definitions aligned with the `ov` CLI. Add new CLI commands to the
 * matching group file; the component derives completion, parameter hints and
 * help from this list.
 */
export const TUI_COMMANDS: readonly TuiCommandDefinition[] = [
  ...CORE_COMMANDS,
  ...FILESYSTEM_COMMANDS,
  ...SESSION_COMMANDS,
];

export interface TuiCommandSection {
  group: TuiCommandGroup;
  commands: readonly TuiCommandDefinition[];
}

/** 按名称或别名查找命令。Finds a command by name or alias. */
export function findTuiCommand(name: string): TuiCommandDefinition | undefined {
  const normalized = name.trim().replace(/^\//, '').toLowerCase();
  if (!normalized) return undefined;
  return TUI_COMMANDS.find(
    (command) =>
      command.name.toLowerCase() === normalized ||
      command.aliases?.some((alias) => alias.toLowerCase() === normalized),
  );
}

/** 按命令名与说明过滤候选，空关键字返回全部。Filters commands by keyword. */
export function filterTuiCommands(
  keyword: string,
): readonly TuiCommandDefinition[] {
  const normalized = keyword.trim().toLowerCase();
  if (!normalized) return TUI_COMMANDS;
  return TUI_COMMANDS.filter(
    (command) =>
      command.name.toLowerCase().includes(normalized) ||
      command.description.toLowerCase().includes(normalized),
  );
}

/** 按分组整理命令，空分组不返回。Groups commands, dropping empty groups. */
export function groupTuiCommands(
  commands: readonly TuiCommandDefinition[],
): readonly TuiCommandSection[] {
  return TUI_COMMAND_GROUPS.map((group) => ({
    group,
    commands: commands.filter((command) => command.group === group.key),
  })).filter((section) => section.commands.length > 0);
}
