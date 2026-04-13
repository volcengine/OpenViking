import type { Message } from './message'

/** SSE event types from vikingbot OpenAPIChannel. */
export type ChatStreamEventType =
  | 'response'
  | 'tool_call'
  | 'tool_result'
  | 'reasoning'
  | 'iteration'
  | 'content_delta'
  | 'reasoning_delta'

/** Single SSE event (ChatStreamEvent.model_dump_json() output). */
export interface ChatStreamEvent {
  event: ChatStreamEventType
  data: unknown
  timestamp: string
}

/** POST /bot/v1/chat/stream request body. */
export interface BotChatRequest {
  message: string
  session_id?: string
  user_id?: string
  stream?: boolean
  need_reply?: boolean
  channel_id?: string
}

/** POST /bot/v1/chat non-streaming response. */
export interface BotChatResponse {
  session_id: string
  message: string
  events?: Array<Record<string, unknown>>
  timestamp: string
}

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
