/** Text content part. */
export interface TextPart {
  type: 'text'
  text: string
}

/** Reasoning/thinking content part used by the chat UI during streaming. */
export interface ReasoningPart {
  type: 'reasoning'
  reasoning: string
  is_running?: boolean
}

/** Agent-loop iteration marker used by the event timeline UI. */
export interface IterationPart {
  type: 'iteration'
  iteration: number
}

/** Context reference part (memory / resource / skill). */
export interface ContextPart {
  type: 'context'
  uri: string
  context_type: 'memory' | 'resource' | 'skill'
  abstract: string
}

/** Tool call/result part. */
export interface ToolPart {
  type: 'tool'
  tool_id: string
  tool_name: string
  tool_uri: string
  skill_uri: string
  tool_status: 'pending' | 'running' | 'completed' | 'error'
  tool_input?: Record<string, unknown>
  tool_output?: string
  duration_ms?: number
  prompt_tokens?: number
  completion_tokens?: number
}

/** Tool result event kept separately to preserve SSE arrival order in the UI. */
export interface ToolResultPart {
  type: 'tool_result'
  tool_id: string
  tool_name: string
  tool_output: string
  is_error: boolean
}

export type MessagePart =
  | TextPart
  | ReasoningPart
  | IterationPart
  | ContextPart
  | ToolPart
  | ToolResultPart

/** A single message in a session (matches backend Message.to_dict()). */
export interface Message {
  id: string
  role: 'user' | 'assistant'
  parts: MessagePart[]
  created_at: string
}
