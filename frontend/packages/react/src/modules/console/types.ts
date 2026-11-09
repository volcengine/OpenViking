import type { HTMLAttributes, ReactNode } from 'react';
import type { OvRequestOptions } from '../../client/types.js';
import type { TuiLabels, TuiRecord } from '../../components/tui/index.js';
import type { VikingBotLabels } from '../../components/viking-bot/index.js';

export type ConsoleMode = 'conversation' | 'command';

export interface ConsoleSlots {
  toolbar?: ReactNode;
  conversationContext?: ReactNode;
  commandHelp?: ReactNode;
}

export interface ConsoleLabels {
  /** Conversation availability notice; replaces the former placeholder copy. */
  description?: string;
  conversation?: string;
  command?: string;
  clear?: string;
  history?: string;
  emptyHistory?: string;
  commandTitle?: string;
  commandDescription?: string;
  quickStart?: string;
  mode?: string;
  tui?: TuiLabels;
  vikingBot?: VikingBotLabels;
}

/** Options shared by Console and its internal Hook. */
export interface ConsoleOptions {
  mode?: ConsoleMode;
  defaultMode?: ConsoleMode;
  locale?: 'en' | 'zh';
  /** Change with the identity when using dynamic credential/identity functions. */
  scopeKey?: string;
  onModeChange?: (mode: ConsoleMode) => void;
  requestOptions?: OvRequestOptions;
}

export interface ConsoleProps
  extends
    ConsoleOptions,
    Omit<HTMLAttributes<HTMLDivElement>, 'onSelect' | 'children'> {
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
