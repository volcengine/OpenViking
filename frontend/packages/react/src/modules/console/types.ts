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
  VikingBotReferences,
} from '../../components/viking-bot/index.js';

export type ConsoleMode = 'conversation' | 'command';

/** Whether the Bot runtime is ready for an eligible library. */
export type ConsoleBotActivationStatus = 'not-started' | 'ready';

/** Optional lifecycle managed by the host for the current library and identity. */
export interface ConsoleBotActivation {
  /** Resolved startup result for the current scope. Console skips its initial lookup when supplied. */
  initialState?:
    | { status: ConsoleBotActivationStatus }
    | { status: 'error'; error: unknown };
  /**
   * Confirm whether the current scope allows Bot use. The tab stays visible while
   * checking or on failure; false hides it, while true proceeds to runtime readiness.
   */
  isEligible(signal: AbortSignal): Promise<boolean>;
  /** Read runtime readiness after the library has allowed access. */
  getStatus(signal: AbortSignal): Promise<ConsoleBotActivationStatus>;
  /** Start the Bot after the user selects Enable. Console checks status again after it resolves. */
  start(signal: AbortSignal): Promise<void>;
  /** Optional introduction media. Console renders an illustration by default. */
  media?: ReactNode;
}

export interface ConsoleCommandGuide {
  description: string;
  examples: readonly [string, ...string[]];
  quickStart?: CommandConsoleCommandDefinition['quickStart'];
}

export interface ConsoleSlots {
  /** 渲染异常时替换失败区域。Replaces a section after a rendering failure. */
  errorFallback?: ErrorFallback;
  toolbar?: ReactNode;
  /** 替换 Bot 启用页的媒体、标题和说明，不含按钮；undefined 使用默认内容，null 隐藏介绍。Replaces activation media and copy, excluding actions; undefined uses the default, null hides the introduction. */
  activationIntro?: ReactNode;
  conversationContext?: ReactNode;
  commandHelp?: ReactNode;
}

export interface ConsoleLabels {
  /** Conversation welcome description. */
  description?: string;
  /** Bot 启用页的默认标题，空字符串隐藏。Default activation heading; an empty string hides it. */
  activationTitle?: string;
  /** Bot 启用页的默认说明，空字符串隐藏。Default activation description; an empty string hides it. */
  activationDescription?: string;
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
  /** Gate chat on a host-managed Bot lifecycle. Keep the adapter stable for the current scope. */
  activation?: ConsoleBotActivation;
  hitl?: import('./hitl/types.js').ConsoleHitlOptions | false;
  /** Set false to hide the built-in session commit action. */
  memoryCommit?: false;
  /** Control selected references while Console loads the picker tree and search. Console notifies the host to remove submitted references and clears selection when resetting the conversation. */
  referenceSelection?: Pick<VikingBotReferences, 'selected' | 'onChange'>;
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

export interface ConsoleCommandFiles {
  pickFile(options: {
    name: string;
    accept?: string;
    signal?: AbortSignal;
  }): Promise<File | undefined>;
  download(blob: Blob, filename: string): void | Promise<void>;
}

export interface ConsoleCommandOptions {
  /** Administrator commands require explicit opt-in and server-side authorization. */
  allowAdminCommands?: boolean;
  /** The default uses the browser's file picker and downloads. */
  files?: ConsoleCommandFiles;
  /** Open the host's local upload page for add-resource without explicit flags. */
  onOpenLocalResourceImport?: () => void;
  /** The host can reuse its OVPack import dialog to confirm the local file. */
  choosePackFile?: (
    targetUri: string,
    signal?: AbortSignal,
  ) => Promise<File | undefined>;
  onPackImported?: (uri: string) => void;
}

export interface ConsoleHistoryEntry {
  id: string;
  command: string;
  createdAtMs: number;
}

/** Options shared by Console and useConsole. */
export interface ConsoleOptions {
  /** Task queries use native HTTP by default; hosts can adapt another backend. */
  taskService?: ConsoleTaskService;
  commandOptions?: ConsoleCommandOptions;
  locale?: Locale;
  /** Enabled modes, in toolbar order. Omitted enables both; an empty list renders no panels. */
  modes?: readonly ConsoleMode[];
  mode?: ConsoleMode;
  /** Initial uncontrolled mode. Defaults to VikingBot ('conversation'). */
  defaultMode?: ConsoleMode;
  /** Change with the identity when using dynamic credential/identity functions. */
  scopeKey?: string;
  /** Opt in to browser-local command history. Provide a stable, credential-free key for the environment, library, and user. */
  commandHistoryKey?: string;
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
   * 终端空态及有记录时输入框上方的快捷入口，按 name 从命令目录复用说明。
   * 点击原样填入 value 并聚焦，不提交。
   * 未命中的名称跳过，
   * 重复名称只保留首次出现。
   * 省略时使用包含 ov 命令前缀的默认集合；传空数组隐藏快捷入口和内置空态。
   * Terminal shortcuts appear in the empty state and above the input once a
   * record exists. They resolve copy by name from the command catalogue;
   * selection fills value verbatim and focuses without submitting. Unknown
   * names are skipped. Omitted uses the default set with ov command prefixes;
   * an empty array hides the shortcuts and the built-in empty state.
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

/** Terminal state with recall history separate from execution records. */
export interface ConsoleState {
  records: readonly CommandConsoleRecord[];
  history: readonly ConsoleHistoryEntry[];
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
