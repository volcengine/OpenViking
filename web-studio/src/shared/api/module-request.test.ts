// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { requestStudioModule } from './module-request'

const request = vi.hoisted(() => vi.fn())
vi.mock('#/shared/api/ov-client/index', () => ({
  getOvClient: () => ({ instance: { request } }),
  isOvClientError: () => false,
}))
afterEach(() => vi.clearAllMocks())

it('exposes SSE chunks before completion through the scoped Axios fetch adapter', async () => {
  let writer!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      writer = controller
    },
  })
  request.mockResolvedValue({
    status: 200,
    data: body,
    headers: { 'content-type': 'text/event-stream' },
  })
  const signal = new AbortController().signal
  const response = await requestStudioModule('/bot/v1/chat/stream', {
    method: 'POST',
    headers: { accept: 'text/event-stream' },
    body: JSON.stringify({ session_id: 'test', message: 'hello' }),
    signal,
  })
  expect(request).toHaveBeenCalledWith(
    expect.objectContaining({
      url: '/bot/v1/chat/stream',
      adapter: 'fetch',
      responseType: 'stream',
      signal,
    }),
  )
  expect(response.headers.get('content-type')).toBe('text/event-stream')
  const reader = response.body!.getReader()
  writer.enqueue(
    new TextEncoder().encode(
      'data: {"type":"reasoning","content":"thinking"}\n\n',
    ),
  )
  const first = await reader.read()
  expect(first.done).toBe(false)
  expect(new TextDecoder().decode(first.value)).toContain('thinking')
  writer.close()
  expect((await reader.read()).done).toBe(true)
})

it('preserves a non-SSE response type so the module can reject an invalid stream', async () => {
  request.mockResolvedValue({
    status: 200,
    data: new ReadableStream({
      start(c) {
        c.close()
      },
    }),
    headers: { 'content-type': 'application/json' },
  })
  const response = await requestStudioModule('/bot/v1/chat/stream', {
    headers: { accept: 'text/event-stream' },
  })
  expect(response.headers.get('content-type')).toBe('application/json')
})
