import { describe, expect, it } from 'vitest'

import { parseSseStream } from './sse'
import type { SseMessage } from '#/lib/sse'

function sseMessage(data: unknown): SseMessage {
  return {
    data: JSON.stringify(data),
    event: 'message',
    id: '',
    retry: null,
  } as SseMessage
}

async function collect(messages: SseMessage[]) {
  const events = []
  for await (const event of parseSseStream(messages)) events.push(event)
  return events
}

describe('parseSseStream provider recovery', () => {
  it('passes a provider_recovery event through with its structured payload', async () => {
    const events = await collect([
      sseMessage({
        event: 'provider_recovery',
        data: { reason: 'primary_failed', error_kind: 'rate_limit_error' },
        timestamp: '2026-10-16T00:00:00Z',
      }),
    ])

    expect(events).toHaveLength(1)
    expect(events[0].event).toBe('provider_recovery')
    expect(events[0].data).toEqual({
      reason: 'primary_failed',
      error_kind: 'rate_limit_error',
    })
  })

  it('keeps ignoring unknown event types (older-consumer safety)', async () => {
    const events = await collect([
      sseMessage({ event: 'future_event', data: 'x' }),
      sseMessage({ event: 'content_delta', data: 'hi' }),
    ])

    expect(events.map((event) => event.event)).toEqual(['content_delta'])
  })
})
