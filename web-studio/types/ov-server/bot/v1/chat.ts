export type ChatStreamEventType =
  | 'response'
  | 'tool_call'
  | 'tool_result'
  | 'reasoning'
  | 'iteration'
  | 'content_delta'
  | 'reasoning_delta'

export type BotChatRequest = {
  channel_id?: string
  message: string
  need_reply?: boolean
  session_id?: string
  stream?: boolean
  user_id?: string
}

export type BotChatResponse = {
  events?: Array<Record<string, unknown>>
  message: string
  session_id: string
  timestamp: string
}

export type BotChatStreamEvent<TEvent extends ChatStreamEventType = ChatStreamEventType> = {
  data: unknown
  event: TEvent
  timestamp: string
}

export type ChatStreamEvent = {
  [TEvent in ChatStreamEventType]: BotChatStreamEvent<TEvent>
}[ChatStreamEventType]
