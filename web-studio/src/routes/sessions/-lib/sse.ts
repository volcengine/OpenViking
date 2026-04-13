import type { ChatStreamEvent, ChatStreamEventType } from '../-types/chat'

const VALID_EVENT_TYPES = new Set<string>([
  'response', 'tool_call', 'tool_result', 'reasoning', 'iteration',
  'content_delta', 'reasoning_delta',
])

function parseSseLine(line: string): ChatStreamEvent | null {
  const trimmed = line.trim()
  if (!trimmed || !trimmed.startsWith('data:')) return null

  const jsonStr = trimmed.slice(5).trim()
  if (!jsonStr) return null

  try {
    const parsed = JSON.parse(jsonStr) as Record<string, unknown>
    if (typeof parsed.event !== 'string' || !VALID_EVENT_TYPES.has(parsed.event)) {
      return null
    }
    return {
      event: parsed.event as ChatStreamEventType,
      data: parsed.data,
      timestamp: typeof parsed.timestamp === 'string' ? parsed.timestamp : new Date().toISOString(),
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
 */
export async function* parseSseStream(response: Response): AsyncGenerator<ChatStreamEvent> {
  const body = response.body
  if (!body) return

  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // SSE events are separated by double newlines
      const parts = buffer.split('\n\n')
      // Keep the last incomplete part in the buffer
      buffer = parts.pop() ?? ''

      for (const part of parts) {
        // Each part may contain multiple lines (e.g. "data: ...\ndata: ...")
        // but our backend sends single-line data events
        for (const line of part.split('\n')) {
          const event = parseSseLine(line)
          if (event) yield event
        }
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
