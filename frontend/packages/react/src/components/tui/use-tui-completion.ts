import { parseTuiCommandInput } from './command-catalog.js';
import {
  filterTuiCommands,
  findTuiCommand,
  groupTuiCommands,
} from './command-definitions.js';
import type { TuiCommandDefinition } from './command-catalog.js';
import type { TuiCommandSection } from './command-definitions.js';

export interface TuiCompletion {
  /** 命令名候选；输入已带空白时为空。Command-name candidates. */
  suggestions: readonly TuiCommandSection[];
  /** 扁平候选，用于键盘序号。Flat candidates for keyboard indexing. */
  options: readonly TuiCommandDefinition[];
  /** 已输入完整命令时的定义。Definition when a full command is typed. */
  activeCommand: TuiCommandDefinition | undefined;
  /** 正在输入命令名（尚未带参数）。Typing a command name without arguments. */
  showTypingHint: boolean;
  /** 已输入命令的参数提示是否展示。Whether the parameter panel shows. */
  showParamHint: boolean;
}

/**
 * 输入补全与参数提示的纯视图逻辑：只根据受控值与命令元数据推导，不发请求。
 *
 * View logic for completion and parameter hints. Everything derives from the
 * controlled value and command metadata; nothing is fetched.
 */
export function useTuiCompletion(
  value: string,
  enabled: boolean,
): TuiCompletion {
  const parsed = parseTuiCommandInput(value);
  const isCommandTyping = Boolean(parsed) && !parsed?.hasTrailingSpace;
  const keyword = isCommandTyping ? (parsed?.name ?? '') : '';
  const options = enabled && isCommandTyping ? filterTuiCommands(keyword) : [];
  const suggestions = groupTuiCommands(options);
  const activeCommand =
    parsed && !isCommandTyping ? findTuiCommand(parsed.name) : undefined;

  return {
    suggestions,
    options,
    activeCommand,
    showTypingHint: enabled && isCommandTyping && keyword.length > 0,
    showParamHint: enabled && Boolean(activeCommand),
  };
}
