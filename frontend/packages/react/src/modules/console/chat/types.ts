import type {
  TaskReference,
  TaskService,
} from '../../../shared/task-tracking/types.js';
import type { OvRequestOptions } from '../../../client/types.js';
import type { Locale } from '../../../shared/locale.js';
import type {
  VikingBotMessage,
  VikingBotMessagePart,
  VikingBotStreamingState,
  VikingBotDraft,
} from '../../../components/viking-bot/model.js';
import type { VikingBotSession } from '../../../components/viking-bot/capabilities.js';

/** Normalized protocol events. Host adapters keep TOP/MA details outside the UI package. */
export type VikingBotChatEvent =
  | { type: 'reasoning'; text: string; delta: boolean; id?: string }
  | { type: 'text'; text: string; delta: boolean }
  | { type: 'iteration'; iteration: number }
  | { type: 'tool'; part: Extract<VikingBotMessagePart, { type: 'tool' }> }
  | {
      type: 'tool-result';
      id: string;
      output: string;
      isError?: boolean;
      tasks?: readonly TaskReference[];
    }
  | { type: 'complete'; text?: string }
  | { type: 'interrupted' };

export interface VikingBotChatInput extends VikingBotDraft {
  sessionId: string;
  text: string;
}

/** All methods receive the operation's signal; adapters must stop when it aborts. */
export interface VikingBotChatService {
  stream: (
    input: VikingBotChatInput,
    signal: AbortSignal,
  ) => AsyncIterable<VikingBotChatEvent>;
  createSession: (signal: AbortSignal) => Promise<VikingBotSession>;
  loadMessages: (
    id: string,
    signal: AbortSignal,
  ) => Promise<readonly VikingBotMessage[]>;
  /** Omit when the backend already persists chat, as in the commercial service. */
  saveMessage?: (
    id: string,
    message: VikingBotMessage,
    signal: AbortSignal,
  ) => Promise<void>;
  interrupt?: (
    id: string,
    signal: AbortSignal,
  ) => Promise<{ interrupted: boolean }>;
  /** Omit when the backend cannot append input to an active streaming turn. */
  steer?: (
    input: VikingBotChatInput,
    signal: AbortSignal,
  ) => Promise<{ accepted: boolean }>;
}

export interface VikingBotSessionService {
  listSessions: (signal: AbortSignal) => Promise<readonly VikingBotSession[]>;
  createSession: (signal: AbortSignal) => Promise<VikingBotSession>;
  removeSession: (id: string, signal: AbortSignal) => Promise<void>;
}

export interface VikingBotHookOptions {
  locale?: Locale;
  /** False keeps the hook inert, including initial history/list requests. */
  enabled?: boolean;
  /** Change alongside a function-valued identity when switching accounts. */
  scopeKey?: string;
  requestOptions?: OvRequestOptions;
}

export interface UseVikingBotChatOptions extends VikingBotHookOptions {
  /** Adapts task queries; native HTTP is the default. Shared by Console when omitted. */
  taskService?: TaskService;
  sessionId?: string;
  initialMessages?: readonly VikingBotMessage[];
  /** Defaults to true. Native session persistence does not store reasoning. */
  persist?: boolean;
  service?: VikingBotChatService;
}

export interface VikingBotChatState {
  sessionId?: string;
  messages: readonly VikingBotMessage[];
  streaming?: VikingBotStreamingState;
  status: 'idle' | 'loading' | 'streaming' | 'error';
  /** A follow-up is awaiting acceptance; the original stream remains active. */
  isSubmitting: boolean;
  error?: Error;
  /** Persistence failure never changes a completed response into a failed send. */
  persistenceError?: Error;
}

export interface VikingBotChatResult extends VikingBotChatState {
  /** True only when this service supplies an actual interrupt operation. */
  canInterrupt: boolean;
  canSteer: boolean;
  send: (input: string | VikingBotDraft) => Promise<boolean>;
  abort: () => Promise<void>;
  reset: () => void;
  selectSession: (sessionId: string) => void;
  reload: () => Promise<void>;
  setMessages: (messages: readonly VikingBotMessage[]) => void;
}

export interface UseVikingBotSessionsOptions extends VikingBotHookOptions {
  service?: VikingBotSessionService;
}

export interface VikingBotSessionsState {
  sessions: readonly VikingBotSession[];
  isLoading: boolean;
  error?: Error;
}

export interface VikingBotSessionsResult extends VikingBotSessionsState {
  refresh: () => Promise<void>;
  create: () => Promise<VikingBotSession | undefined>;
  remove: (id: string) => Promise<boolean>;
}
