/** 等待中的记录按命令选择的骨架。Skeleton chosen for a pending record. */
export type CommandConsoleSkeletonKind =
  'search' | 'resource' | 'tree' | 'status' | 'write' | 'generic';

export interface CommandConsoleCommandGroup {
  key: string;
  label: string;
}

export interface CommandConsoleCommandParam {
  label: string;
  description: string;
  required?: boolean;
  defaultValue?: string;
}

export interface CommandConsoleCommandOption {
  token: string;
  description: string;
  required?: boolean;
  defaultValue?: string;
}

export interface CommandConsoleCommandExample {
  value: string;
  label?: string;
}

/**
 * 一条 CLI 命令的元数据。名称、参数、说明与示例由宿主提供，
 * 组件用它生成补全、参数提示和等待骨架。
 *
 * Metadata of one CLI command. Names, arguments, copy and examples come from
 * the host; the component derives completion, parameter hints and
 * pending skeletons from them.
 */
export interface CommandConsoleCommandDefinition {
  name: string;
  /** 接受补全时填入的真实前缀，默认 name；例如由宿主提供 ov read。 */
  inputText?: string;
  aliases?: readonly string[];
  group?: string;
  /** 保留输入识别，从候选列表隐藏。Keeps input matching while hiding suggestions. */
  hiddenFromSuggestions?: boolean;
  description: string;
  usage: string;
  summary?: string;
  params?: readonly CommandConsoleCommandParam[];
  options?: readonly CommandConsoleCommandOption[];
  examples?: readonly (string | CommandConsoleCommandExample)[];
  /** 由文字和可选命令组成的快速体验。Steps with optional commands to fill the input. */
  quickStart?: {
    documentation?: { label: string; href: string };
    steps: readonly { description: string; command?: string }[];
  };
  /** 覆盖引导面板的用法和参数，输入补全仍使用本命令的参数。Help-only syntax and parameters. */
  parameterDetails?: {
    usage: string;
    options?: readonly CommandConsoleCommandOption[];
    extra?: {
      description: string;
      options: readonly CommandConsoleCommandOption[];
    };
  };
  skeleton?: CommandConsoleSkeletonKind;
  /** Resolves URI values and their character ranges in the text after the command prefix. */
  resolveUriArguments?: (
    text: string,
  ) => readonly { start: number; end: number; uri: string }[];
}

export interface CommandConsoleParsedCommandInput {
  /** 命令名，不含开头的斜杠。Command name without the leading slash. */
  name: string;
  /** 命令名之后的内容。Everything after the command name. */
  rest: string;
  /** 命令名后是否已输入空白。Whether whitespace follows the command name. */
  hasTrailingSpace: boolean;
}

const LEADING_SLASH = /^\s*\//;
const COMMAND_NAME = /^([^\s]*)([\s\S]*)$/;

/** 解析 `/name args`；不以斜杠开头时返回 `null`。Parses `/name args`. */
export function parseCommandConsoleCommandInput(
  value: string,
): CommandConsoleParsedCommandInput | null {
  if (!LEADING_SLASH.test(value)) return null;
  const body = value.replace(LEADING_SLASH, '');
  const matched = COMMAND_NAME.exec(body);
  if (!matched) return null;
  const name = matched[1] ?? '';
  const rest = matched[2] ?? '';
  return { name, rest, hasTrailingSpace: /^\s/.test(rest) };
}

/** 取出命令名，忽略参数与斜杠。Resolves the command name of any input. */
export function resolveCommandConsoleCommandName(value: string): string {
  const parsed = parseCommandConsoleCommandInput(value);
  if (parsed) return parsed.name.toLowerCase();
  return (value.trim().split(/\s+/)[0] ?? '').replace(/^\//, '').toLowerCase();
}

/** 骨架由宿主的命令描述提供；未知命令使用通用形态。 */
export function resolveCommandConsoleSkeleton(
  command: CommandConsoleCommandDefinition | undefined,
): CommandConsoleSkeletonKind {
  return command?.skeleton ?? 'generic';
}
