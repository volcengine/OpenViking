/** 等待中的记录按命令选择的骨架。Skeleton chosen for a pending record. */
export type TuiSkeletonKind =
  'search' | 'resource' | 'tree' | 'status' | 'write' | 'generic';

export interface TuiCommandGroup {
  key: string;
  label: string;
}

export interface TuiCommandParam {
  label: string;
  description: string;
  required?: boolean;
  defaultValue?: string;
}

export interface TuiCommandOption {
  token: string;
  description: string;
  required?: boolean;
  defaultValue?: string;
}

/**
 * 一条 CLI 命令的元数据。名称、参数、说明与示例由宿主提供，
 * 组件用它生成补全、参数提示和等待骨架。
 *
 * Metadata of one CLI command. Names, arguments, copy and examples come from
 * the host; the component derives completion, parameter hints and
 * pending skeletons from them.
 */
export interface TuiCommandExample {
  value: string;
  label?: string;
}

export interface TuiCommandDefinition {
  name: string;
  /** 接受补全时填入的真实前缀，默认 name；例如由宿主提供 ov read。 */
  inputText?: string;
  aliases?: readonly string[];
  group?: string;
  description: string;
  usage: string;
  summary?: string;
  params?: readonly TuiCommandParam[];
  options?: readonly TuiCommandOption[];
  examples?: readonly (string | TuiCommandExample)[];
  skeleton?: TuiSkeletonKind;
}

export interface TuiParsedCommandInput {
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
export function parseTuiCommandInput(
  value: string,
): TuiParsedCommandInput | null {
  if (!LEADING_SLASH.test(value)) return null;
  const body = value.replace(LEADING_SLASH, '');
  const matched = COMMAND_NAME.exec(body);
  if (!matched) return null;
  const name = matched[1] ?? '';
  const rest = matched[2] ?? '';
  return { name, rest, hasTrailingSpace: /^\s/.test(rest) };
}

/** 取出命令名，忽略参数与斜杠。Resolves the command name of any input. */
export function resolveTuiCommandName(value: string): string {
  const parsed = parseTuiCommandInput(value);
  if (parsed) return parsed.name.toLowerCase();
  return (value.trim().split(/\s+/)[0] ?? '').replace(/^\//, '').toLowerCase();
}

/** 骨架由宿主的命令描述提供；未知命令使用通用形态。 */
export function resolveTuiSkeleton(
  command: TuiCommandDefinition | undefined,
): TuiSkeletonKind {
  return command?.skeleton ?? 'generic';
}
