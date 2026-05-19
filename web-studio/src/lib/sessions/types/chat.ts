import type { Message } from './message'

export type {
  BotChatRequest,
  BotChatResponse,
  BotChatStreamEvent,
  ChatStreamEvent,
  ChatStreamEventType,
} from '@ov-server/bot/v1/chat'

/** Chat status for the useChat hook. */
export type ChatStatus = 'idle' | 'streaming' | 'error'

/** Tracked tool call during a streaming round. */
export interface StreamToolCall {
  name: string
  arguments: string
  result?: string
}

/** Full chat state exposed by useChat. */
export interface ChatState {
  messages: Message[]
  status: ChatStatus
  error?: string
  /** Accumulated text content during streaming (before final response). */
  streamingContent: string
  /** Tool calls observed during the current streaming round. */
  streamingToolCalls: StreamToolCall[]
  /** Reasoning content from the current streaming round. */
  streamingReasoning: string
  /** Current iteration index. */
  iteration: number
}
