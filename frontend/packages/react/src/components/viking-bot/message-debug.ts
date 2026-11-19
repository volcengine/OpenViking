import type { VikingBotMessage } from './model.js';
import { messageTimestampSource } from './message-timestamp-source.js';

/** 宿主提供的排查标识，不传入凭据。Host diagnostic identifiers, without credentials. */
export interface VikingBotDebugContext {
  /** 当前会话标识。Current session identifier. */
  sessionId?: string;
  /** Agent 标识。Agent identifier. */
  agentId?: string;
  /** Agent 会话标识。Agent session identifier. */
  agentSessionId?: string;
  /** 工作区标识。Workspace identifier. */
  workspaceId?: string;
  /** 账号标识。Account identifier. */
  accountId?: string;
  /** 用户标识。User identifier. */
  userId?: string;
  /** 排查模式名称。Diagnostic mode name. */
  mode?: string;
}

export interface VikingBotDebugLabels {
  copyMessageDebug?: string;
  copySessionDebug?: string;
  debugCopied?: string;
  debugCopyFailed?: string;
}

export const withSessionDebugContext = (
  context?: VikingBotDebugContext,
  sessionId?: string,
): VikingBotDebugContext => ({
  ...context,
  sessionId: sessionId ?? context?.sessionId,
});

const identifier = (value: unknown) =>
  typeof value === 'string' ? value : undefined;

const metadata = (message: VikingBotMessage) => ({
  message_id: message.id,
  turn_id: message.turnId,
  role: message.role,
  created_at: message.createdAt,
  is_streaming: Boolean(message.streaming),
  interrupted: Boolean(message.interrupted),
});

/** Build only an explicit metadata allowlist, on the user's copy action. */
export function createMessageDebugText({
  message,
  messages,
  context,
  isStreaming,
}: {
  message: VikingBotMessage;
  messages: readonly VikingBotMessage[];
  context?: VikingBotDebugContext;
  isStreaming: boolean;
}) {
  const loadedSession = message.role === 'user';
  return JSON.stringify(
    {
      type: loadedSession
        ? 'vikingbot_loaded_session_debug'
        : 'vikingbot_message_debug',
      captured_at: new Date().toISOString(),
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      session: {
        session_id: identifier(context?.sessionId),
        agent_id: identifier(context?.agentId),
        agent_session_id: identifier(context?.agentSessionId),
        workspace_id: identifier(context?.workspaceId),
        account_id: identifier(context?.accountId),
        user_id: identifier(context?.userId),
        mode: identifier(context?.mode),
        is_streaming: isStreaming,
      },
      message: metadata(messageTimestampSource(message)),
      ...(loadedSession
        ? { scope: 'loaded_messages', messages: messages.map(metadata) }
        : {}),
    },
    null,
    2,
  );
}
