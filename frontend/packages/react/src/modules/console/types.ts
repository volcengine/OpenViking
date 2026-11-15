import type { TaskService } from '../../shared/task-tracking/types.js';
export type ConsoleTaskService = TaskService;
export type { TaskSnapshot as ConsoleTaskSnapshot } from '../../shared/task-tracking/types.js';
import type { ErrorFallback } from '../../shared/error-boundary/types.js';
import type { Locale } from '../../shared/locale.js';
import type { HTMLAttributes, ReactNode, SetStateAction } from 'react';
import type { OvRequestOptions } from '../../client/types.js';
import type {
  UseVikingBotChatOptions,
  VikingBotChatService,
  VikingBotSessionService,
} from './chat/types.js';
import type {
  CommandConsoleCommandDefinition,
  CommandConsoleLabels,
  CommandConsoleQuickCommand,
  CommandConsoleRecord,
} from '../../components/command-console/index.js';
import type {
  VikingBotLabels,
  VikingBotProps,
} from '../../components/viking-bot/index.js';

export type ConsoleMode = 'conversation' | 'command';

export interface ConsoleCommandGuide {
  description: string;
  examples: readonly [string, ...string[]];
}

export interface ConsoleSlots {
  /** 渲染异常时替换失败区域。Replaces a section after a rendering failure. */
  errorFallback?: ErrorFallback;
  toolbar?: ReactNode;
  conversationContext?: ReactNode;
  commandHelp?: ReactNode;
}

export interface ConsoleLabels {
  /** Conversation welcome description. */
  description?: string;
  conversation?: string;
  command?: string;
  clear?: string;
  history?: string;
  emptyHistory?: string;
  /** 终端空态标题，转交 CommandConsole 的 `emptyTitle`。Terminal empty-state heading, mapped to CommandConsole `emptyTitle`. */
  commandTitle?: string;
  /** 终端空态说明，转交 CommandConsole 的 `emptyDescription`。Terminal empty-state description, mapped to CommandConsole `emptyDescription`. */
  commandDescription?: string;
  /** 快捷命令引导文案，转交 CommandConsole 的 `quickStart`。Quick-command label, mapped to CommandConsole `quickStart`. */
  quickStart?: string;
  mode?: string;
  commandConsole?: CommandConsoleLabels;
  vikingBot?: VikingBotLabels;
}

/** Request and persistence options for Console's built-in conversation. */
export type ConsoleChatOptions = Omit<
  UseVikingBotChatOptions,
  'enabled' | 'sessionId' | 'initialMessages'
> & {
  service?: VikingBotChatService & VikingBotSessionService;
  hitl?: import('./hitl/types.js').ConsoleHitlOptions | false;
};

/**
 * 命令模式的宿主覆盖项；记录、输入、提交和命令目录由 Console 管理。
 * Command-mode host overrides; Console owns records, input, submission and the
 * command catalogue.
 */
export interface ConsoleCommandProps {
  /** 点击资源链接时触发；省略时使用 Console 的 `onOpenUri`。 */
  onOpenUri?: (uri: string) => void;
}

/** Options shared by Console and useConsole. */
export interface ConsoleOptions {
  /** Task queries use native HTTP by default; hosts can adapt another backend. */
  taskService?: ConsoleTaskService;
  locale?: Locale;
  /** Enabled modes, in toolbar order. Omitted enables both; an empty list renders no panels. */
  modes?: readonly ConsoleMode[];
  mode?: ConsoleMode;
  /** Initial uncontrolled mode. Defaults to VikingBot ('conversation'). */
  defaultMode?: ConsoleMode;
  /** Change with the identity when using dynamic credential/identity functions. */
  scopeKey?: string;
  onModeChange?: (mode: ConsoleMode) => void;
  requestOptions?: OvRequestOptions;
}

export interface ConsoleProps
  extends
    ConsoleOptions,
    Omit<HTMLAttributes<HTMLDivElement>, 'onSelect' | 'children'> {
  /** 可选文案语言；继承上层，最终默认英文。Optional locale; inherits or defaults to en-US. */
  locale?: Locale;
  /**
   * 终端空态快捷入口，按 name 从命令目录复用说明，点击原样填入 value 并聚焦，不提交。
   * 未命中的名称跳过，
   * 重复名称只保留首次出现。
   * 省略时使用包含 ov 命令前缀的默认集合；传空数组不展示内置空态。
   * Terminal shortcuts resolve copy by name from the command catalogue;
   * selection fills value verbatim and focuses without submitting. Unknown
   * names are skipped. Omitted uses the default set with ov command prefixes;
   * an empty array shows no built-in empty state.
   */
  quickCommands?: readonly CommandConsoleQuickCommand[];
  /** Native chat by default. Supply a stable service to adapt a non-native backend. */
  chatOptions?: ConsoleChatOptions;
  /** Host-owned conversation state/capabilities. Omit draft callbacks to use Console's local draft. */
  conversation?: Partial<VikingBotProps>;
  /** 命令模式的覆盖项；省略时命令模式使用 Console 的 `onOpenUri`。 */
  command?: ConsoleCommandProps;
  /** 上报导航意图，由宿主打开资源；对话和命令模式各自可用 `conversation.onOpenUri`、`command.onOpenUri` 覆盖。 */
  onOpenUri?: (uri: string) => void;
  slots?: ConsoleSlots;
  labels?: ConsoleLabels;
}

/** Local state. Records and drafts are never persisted by the module. */
export interface ConsoleState {
  records: readonly CommandConsoleRecord[];
  command: string;
  conversationDraft: string;
  isExecuting: boolean;
  error?: unknown;
  failedCommand?: string;
}

/** Data and operations available independently of Console's view. */
export interface ConsoleResult extends ConsoleState {
  mode: ConsoleMode | undefined;
  commands: readonly CommandConsoleCommandDefinition[];
  setMode: (mode: ConsoleMode) => void;
  setCommand: (command: string) => void;
  setConversationDraft: (draft: SetStateAction<string>) => void;
  execute: (command?: string) => Promise<void>;
  retry: () => Promise<void>;
  cancel: () => void;
  clear: () => void;
}
