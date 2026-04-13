import type { ThreadMessageLike } from '@assistant-ui/react'

import type { Message } from '../-types/message'
import type { StreamToolCall } from '../-types/chat'

/** JSON-safe object type matching assistant-ui's expected args shape. */
type JSONObject = { readonly [key: string]: JSONValue }
type JSONValue = string | number | boolean | null | JSONValue[] | { [key: string]: JSONValue }

type ThreadMessageContent = Extract<
  ThreadMessageLike['content'],
  readonly unknown[]
>[number]

/**
 * Convert a completed Message to assistant-ui's ThreadMessageLike format.
 */
export function convertMessage(msg: Message): ThreadMessageLike {
  const content: ThreadMessageContent[] = []

  for (const part of msg.parts) {
    switch (part.type) {
      case 'text':
        if (part.text) {
          content.push({ type: 'text', text: part.text })
        }
        break

      case 'tool': {
        const toolCallId = part.tool_id || `tc_${part.tool_name}_${msg.id}`
        content.push({
          type: 'tool-call',
          toolCallId,
          toolName: part.tool_name,
          args: (part.tool_input ?? {}) as JSONObject,
          result: part.tool_output ?? undefined,
          isError: part.tool_status === 'error',
        })
        break
      }

      case 'context':
        // Context parts (memory/resource/skill references) are not rendered
        // in the message stream. They are injected context, not user-visible.
        break
    }
  }

  return {
    id: msg.id,
    role: msg.role,
    content,
    createdAt: new Date(msg.created_at),
    ...(msg.role === 'assistant' ? { status: { type: 'complete' as const, reason: 'stop' as const } } : {}),
  }
}

/**
 * Build a synthetic in-flight assistant ThreadMessageLike from streaming state.
 */
export function buildStreamingMessage(opts: {
  content: string
  toolCalls: StreamToolCall[]
  reasoning: string
  iteration: number
}): ThreadMessageLike {
  const content: ThreadMessageContent[] = []

  // Reasoning as a reasoning part
  if (opts.reasoning) {
    content.push({ type: 'reasoning', text: opts.reasoning })
  }

  // Tool calls in progress
  for (let i = 0; i < opts.toolCalls.length; i++) {
    const tc = opts.toolCalls[i]
    let args: JSONObject = {}
    try {
      args = JSON.parse(tc.arguments) as JSONObject
    } catch {
      if (tc.arguments) args = { raw: tc.arguments }
    }
    content.push({
      type: 'tool-call',
      toolCallId: `streaming_tc_${i}`,
      toolName: tc.name,
      args,
      result: tc.result ?? undefined,
    })
  }

  // Streaming text content
  if (opts.content) {
    content.push({ type: 'text', text: opts.content })
  }

  return {
    id: 'streaming-msg',
    role: 'assistant',
    content,
    createdAt: new Date(),
    status: { type: 'running' },
    metadata: {
      custom: {
        iteration: opts.iteration,
      },
    },
  }
}
