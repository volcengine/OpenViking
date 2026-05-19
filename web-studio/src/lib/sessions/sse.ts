import type {
  ChatStreamEvent,
  ChatStreamEventType,
} from '@ov-server/bot/v1/chat'

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

function parseSseLine(line: string): ChatStreamEvent | null {
  const trimmed = line.trim()
  if (!trimmed || !trimmed.startsWith('data:')) return null

  const jsonStr = trimmed.slice(5).trim()
  if (!jsonStr) return null

  try {
    const parsed = JSON.parse(jsonStr) as Record<string, unknown>
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
 * Parse an SSE response body into an async generator of ChatStreamEvents.
 *
 * Backend format (from openapi.py):
 *   data: {"event":"response","data":"...","timestamp":"..."}\n\n
 *
 * All events use `data:` prefix. Event type is inside the JSON payload.
 * The OpenViking bot proxy currently forwards non-empty SSE lines, so the
 * browser may receive `data:` lines without the blank separator. Parse each
 * complete data line immediately to keep the chat UI live during streaming.
 */
export async function* parseSseStream(
  response: Response,
): AsyncGenerator<ChatStreamEvent> {
  const body = response.body
  if (!body) return

  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      let newlineIndex = buffer.indexOf('\n')
      while (newlineIndex >= 0) {
        const line = buffer.slice(0, newlineIndex)
        buffer = buffer.slice(newlineIndex + 1)

        const event = parseSseLine(line)
        if (event) yield event

        newlineIndex = buffer.indexOf('\n')
      }
    }

    // Process any remaining buffer
    if (buffer.trim()) {
      for (const line of buffer.split('\n')) {
        const event = parseSseLine(line)
        if (event) yield event
      }
    }
  } finally {
    reader.releaseLock()
  }
}
