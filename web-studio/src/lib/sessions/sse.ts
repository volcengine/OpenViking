import type {
  ChatStreamEvent,
  ChatStreamEventType,
} from '@ov-server/bot/v1/chat'
import type { SseMessage } from '#/lib/sse'

const VALID_EVENT_TYPES = new Set<string>([
  'response',
  'tool_call',
  'tool_result',
  'reasoning',
  'iteration',
  'content_delta',
  'reasoning_delta',
])

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function streamEventDataToText(data: unknown): string {
  if (typeof data === 'string') return data
  if (data == null) return ''
  if (typeof data === 'number' || typeof data === 'boolean') return String(data)

  if (isRecord(data)) {
    const content = data.content
    if (typeof content === 'string') return content

    const error = data.error
    if (typeof error === 'string') return error

    const message = data.message
    if (typeof message === 'string') return message
  }

  try {
    return JSON.stringify(data)
  } catch {
    return String(data)
  }
}

function parseSseMessage(message: SseMessage): ChatStreamEvent | null {
  try {
    const parsed = JSON.parse(message.data) as Record<string, unknown>
    if (
      typeof parsed.event !== 'string' ||
      !VALID_EVENT_TYPES.has(parsed.event)
    ) {
      return null
    }
    return {
      event: parsed.event as ChatStreamEventType,
      data: parsed.data,
      timestamp:
        typeof parsed.timestamp === 'string'
          ? parsed.timestamp
          : new Date().toISOString(),
    }
  } catch {
    return null
  }
}

/**
 * Validate standard SSE messages as chat protocol events.
 *
 * Backend format (from openapi.py):
 *   data: {"event":"response","data":"...","timestamp":"..."}\n\n
 *
 * Event type is inside the JSON payload.
 */
export async function* parseSseStream(
  messages: AsyncIterable<SseMessage>,
): AsyncGenerator<ChatStreamEvent> {
  for await (const message of messages) {
    const event = parseSseMessage(message)
    if (event) yield event
  }
}
