import {
  EventStreamContentType,
  fetchEventSource,
} from '@microsoft/fetch-event-source'

import type {
  EventSourceMessage,
  FetchEventSourceInit,
} from '@microsoft/fetch-event-source'

export type SseMessage = EventSourceMessage

export type FetchSseOptions = Omit<
  FetchEventSourceInit,
  'onopen' | 'onmessage' | 'onclose' | 'onerror' | 'signal'
> & {
  signal?: AbortSignal
}

export class SseResponseError extends Error {
  readonly response: Response
  readonly responseBody: string

  constructor(response: Response, responseBody: string) {
    super(
      response.ok
        ? `Expected ${EventStreamContentType} response`
        : `SSE request failed (${response.status}): ${responseBody}`,
    )
    this.name = 'SseResponseError'
    this.response = response
    this.responseBody = responseBody
  }
}

/**
 * Make a Fetch-compatible SSE request and expose its messages as an async
 * iterable. Unlike the browser EventSource API, this supports POST bodies,
 * custom headers, and AbortSignal.
 */
export async function* fetchSse(
  input: RequestInfo,
  { signal, ...options }: FetchSseOptions = {},
): AsyncGenerator<SseMessage> {
  if (signal?.aborted) {
    throw (
      signal.reason ??
      new DOMException('The operation was aborted', 'AbortError')
    )
  }

  const controller = new AbortController()
  const messages: Array<SseMessage> = []
  const state: { completed: boolean; failure?: unknown } = { completed: false }
  let wakeConsumer: (() => void) | undefined

  const wake = () => {
    wakeConsumer?.()
    wakeConsumer = undefined
  }
  const abort = () => {
    state.failure =
      signal?.reason ??
      new DOMException('The operation was aborted', 'AbortError')
    controller.abort(state.failure)
  }

  signal?.addEventListener('abort', abort, { once: true })

  const request = fetchEventSource(input, {
    ...options,
    signal: controller.signal,
    openWhenHidden: true,
    async onopen(response) {
      const contentType = response.headers.get('content-type') ?? ''
      if (response.ok && contentType.includes(EventStreamContentType)) return

      const responseBody = await response.text().catch(() => '')
      throw new SseResponseError(response, responseBody)
    },
    onmessage(message) {
      messages.push(message)
      wake()
    },
    onerror(error) {
      // Chat POST requests must not be replayed automatically.
      throw error
    },
  }).then(
    () => {
      state.completed = true
      wake()
    },
    (error: unknown) => {
      state.failure = error
      state.completed = true
      wake()
    },
  )

  try {
    while (!state.completed || messages.length > 0) {
      const message = messages.shift()
      if (message) {
        yield message
        continue
      }

      await new Promise<void>((resolve) => {
        wakeConsumer = resolve
      })
    }

    if (state.failure) throw state.failure
    await request
  } finally {
    signal?.removeEventListener('abort', abort)
    controller.abort()
    await request.catch(() => undefined)
  }
}
