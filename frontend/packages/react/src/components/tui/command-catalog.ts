export type TuiCommandGroupKey =
  'core' | 'filesystem' | 'search' | 'status' | 'session' | 'interactive';

/** 等待中的记录按命令选择的骨架。Skeleton chosen for a pending record. */
export type TuiSkeletonKind =
  'search' | 'resource' | 'tree' | 'status' | 'write' | 'generic';

export interface TuiCommandGroup {
  key: TuiCommandGroupKey;
  label: string;
}

export interface TuiCommandParam {
  label: string;
  description: string;
}

export interface TuiCommandOption {
  token: string;
  description: string;
}

/**
 * 一条 CLI 命令的元数据。名称、参数、说明与示例与 CLI 的同一份定义对齐，
 * 组件用它生成补全、参数提示和等待骨架。
 *
 * Metadata of one CLI command. Names, arguments, copy and examples mirror the
 * CLI definitions; the component derives completion, parameter hints and
 * pending skeletons from them.
 */
export interface TuiCommandDefinition {
  name: string;
  aliases?: readonly string[];
  group: TuiCommandGroupKey;
  description: string;
  usage: string;
  params?: readonly TuiCommandParam[];
  options?: readonly TuiCommandOption[];
  examples?: readonly string[];
  skeleton?: TuiSkeletonKind;
}

export const TUI_COMMAND_GROUPS: readonly TuiCommandGroup[] = [
  { key: 'core', label: 'Core workflow' },
  { key: 'filesystem', label: 'Filesystem' },
  { key: 'search', label: 'Search & context' },
  { key: 'status', label: 'Config & status' },
  { key: 'session', label: 'Import, export & sessions' },
  { key: 'interactive', label: 'Interactive & admin' },
];

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

const SKELETON_BY_COMMAND: Readonly<Record<string, TuiSkeletonKind>> = {
  find: 'search',
  search: 'search',
  grep: 'search',
  glob: 'search',
  read: 'resource',
  abstract: 'resource',
  overview: 'resource',
  ls: 'tree',
  tree: 'tree',
  stat: 'status',
  status: 'status',
  health: 'status',
  observer: 'status',
  task: 'status',
  wait: 'status',
  config: 'status',
  write: 'write',
  mkdir: 'write',
  rm: 'write',
  cp: 'write',
  mv: 'write',
  get: 'write',
  'add-resource': 'write',
  'add-skill': 'write',
  'add-memory': 'write',
  import: 'write',
  export: 'write',
  backup: 'write',
  restore: 'write',
  compile: 'write',
};

/** 等待中的记录使用的骨架；未识别的命令使用通用骨架。 */
export function resolveTuiSkeleton(
  commandText: string,
  command: TuiCommandDefinition | undefined,
): TuiSkeletonKind {
  const name = resolveTuiCommandName(commandText);
  return command?.skeleton ?? SKELETON_BY_COMMAND[name] ?? 'generic';
}
