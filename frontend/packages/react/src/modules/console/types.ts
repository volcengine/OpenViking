import type { Locale } from '../../shared/locale.js';
import type { HTMLAttributes, ReactNode } from 'react';
import type { OvRequestOptions } from '../../client/types.js';
import type {
  TuiCommandDefinition,
  TuiLabels,
  TuiRecord,
} from '../../components/tui/index.js';
import type {
  VikingBotLabels,
  VikingBotProps,
} from '../../components/viking-bot/index.js';

export type ConsoleMode = 'conversation' | 'command';

export interface ConsoleSlots {
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
  /** 终端空态标题，转交 Tui 的 `emptyTitle`。Terminal empty-state heading, mapped to Tui `emptyTitle`. */
  commandTitle?: string;
  /** 终端空态说明，转交 Tui 的 `emptyDescription`。Terminal empty-state description, mapped to Tui `emptyDescription`. */
  commandDescription?: string;
  /** 快捷命令引导文案，转交 Tui 的 `quickStart`。Quick-command label, mapped to Tui `quickStart`. */
  quickStart?: string;
  mode?: string;
  tui?: TuiLabels;
  vikingBot?: VikingBotLabels;
}

/** Options shared by Console and useConsole. */
export interface ConsoleOptions {
  locale?: Locale;
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
  /** 内置中英文文案的语言。Language of the built-in interface copy. */
  locale: Locale;
  /**
   * 终端空态展示的快捷命令名称，按数组顺序取自命令目录，未命中的名称跳过，
   * 重复名称只保留首次出现。
   * 省略时使用默认集合；传空数组不展示内置空态。
   * Quick command names shown in the terminal empty state, resolved from the
   * command catalogue in array order with unknown names skipped. Omitted uses
   * the default set; an empty array shows no built-in empty state.
   */
  quickCommands?: readonly string[];
  /** Host-owned conversation state/capabilities. Omit draft callbacks to use Console's local draft. */
  conversation?: Partial<VikingBotProps>;
  /** Reports navigation intent; the host opens the resource. */
  onOpenUri?: (uri: string) => void;
  slots?: ConsoleSlots;
  labels?: ConsoleLabels;
}

/** Local state. Records and drafts are never persisted by the module. */
export interface ConsoleState {
  records: readonly TuiRecord[];
  command: string;
  conversationDraft: string;
  isExecuting: boolean;
  error?: unknown;
  failedCommand?: string;
}

/** Data and operations available independently of Console's view. */
export interface ConsoleResult extends ConsoleState {
  mode: ConsoleMode;
  commands: readonly TuiCommandDefinition[];
  setMode: (mode: ConsoleMode) => void;
  setCommand: (command: string) => void;
  setConversationDraft: (draft: string) => void;
  execute: (command?: string) => Promise<void>;
  retry: () => Promise<void>;
  cancel: () => void;
  clear: () => void;
}
