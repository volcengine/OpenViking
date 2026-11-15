import type { Spec } from './event-spec.js';
import type {
  OperationFault,
  SessionListReady,
  SessionReady,
  HistoryReady,
  FirstContentReady,
  ReplyReady,
  ReplyFault,
  PersistenceFault,
} from './views.js';
export type ConsoleEvents = {
  readonly console_session_list_load_ready: Spec<
    'Console',
    SessionListReady,
    'sessions'
  >;
  readonly console_session_list_load_fail: Spec<
    'Console',
    OperationFault,
    'sessions'
  >;
  readonly console_session_create_success: Spec<
    'Console',
    SessionReady,
    'sessions'
  >;
  readonly console_session_create_fail: Spec<
    'Console',
    OperationFault,
    'sessions'
  >;
  readonly console_session_history_load_ready: Spec<
    'Console',
    HistoryReady,
    'sessions'
  >;
  readonly console_session_history_load_fail: Spec<
    'Console',
    OperationFault,
    'sessions'
  >;
  readonly console_session_delete_success: Spec<
    'Console',
    SessionReady,
    'sessions'
  >;
  readonly console_session_delete_fail: Spec<
    'Console',
    OperationFault,
    'sessions'
  >;
  readonly console_chat_first_content_ready: Spec<
    'Console',
    FirstContentReady,
    'chat'
  >;
  readonly console_chat_reply_complete: Spec<'Console', ReplyReady, 'chat'>;
  readonly console_chat_reply_fail: Spec<'Console', ReplyFault, 'chat'>;
  readonly console_chat_history_save_fail: Spec<
    'Console',
    PersistenceFault,
    'persistence'
  >;
};
