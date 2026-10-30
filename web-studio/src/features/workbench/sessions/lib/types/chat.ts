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
  iteration?: number
  result?: string
}
