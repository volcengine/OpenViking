import axios from 'axios'
import { afterEach, expect, it, vi } from 'vitest'
import {
  createConfigFileApi,
  embeddingPatch,
  isConfigFileObject,
} from './config-file-api'
import type { InternalAxiosRequestConfig } from 'axios'

afterEach(() => vi.restoreAllMocks())
it('sends only editable embedding fields and retains credential extensions supported by the account schema', () => {
  expect(
    embeddingPatch({
      max_retries: 4,
      max_input_tokens: 1000,
      text_source: 'content_only',
      dense: {
        model: 'frozen',
        dimension: 1024,
        input: 'text',
        api_key: 'parent-key',
        failback_request_count: 7,
        credentials: [
          {
            provider: 'azure',
            api_key: 'key',
            model: 'deployment',
            api_version: 'v1',
            extra_headers: { keep: 'yes' },
            dimension: 2048,
          },
        ],
      },
    }),
  ).toEqual({
    max_retries: 4,
    dense: {
      failback_request_count: 7,
      credentials: [
        {
          provider: 'azure',
          api_key: 'key',
          model: 'deployment',
          api_version: 'v1',
          extra_headers: { keep: 'yes' },
        },
      ],
    },
  })
})
it('uses ROOT credentials to preview a full draft without saving, then saves one full revision', async () => {
  const requests: InternalAxiosRequestConfig[] = []
  vi.spyOn(axios, 'create').mockReturnValue(
    axios.create({
      adapter: async (config) => {
        requests.push(config)
        return {
          status: 200,
          statusText: 'OK',
          headers: {},
          config,
          data: {
            status: 'ok',
            result: {
              revision: 'file-revision',
              content: '{"server":{"port":1933}}',
              models: Object.fromEntries(
                ['vlm', 'embedding'].map((kind) => [kind, { config: {} }]),
              ),
            },
          },
        }
      },
    }),
  )
  const api = createConfigFileApi(
    {
      baseUrl: 'http://localhost:1933',
      accountId: 'default',
      userId: 'default',
      adminApiKey: 'root-key',
      apiKey: 'user-key',
    },
    false,
  )
  const loaded = await api.get()
  const draft = await api.preview(loaded.content, {
    embedding: { max_retries: 2 },
  })
  await api.save(draft.content, loaded.revision)
  expect(
    requests.every(
      (request) => request.headers.get('X-API-Key') === 'root-key',
    ),
  ).toBe(true)
  expect(requests[0].url).toContain('source=file')
  expect(requests[0].url).not.toContain('/accounts/')
  expect(requests[1].method).toBe('patch')
  expect(requests[1].url).toContain('/api/v1/admin/configuration?source=file')
  expect(requests[1].url).toContain('dry_run=true')
  expect(JSON.parse(requests[1].data)).toEqual({
    content: loaded.content,
    settings: { embedding: { max_retries: 2 } },
  })
  expect(requests).toHaveLength(3)
  expect(requests[2].method).toBe('patch')
  expect(requests[2].url).toContain('/admin/configuration?source=file')
  expect(JSON.parse(requests[2].data)).toEqual({
    revision: 'file-revision',
    content: loaded.content,
  })
  await api.restart(loaded.revision)
  expect(requests[3].url).toBe('http://localhost:1933/api/v1/admin/restart')
  expect(requests[3].method).toBe('post')
  expect(JSON.parse(requests[3].data)).toEqual({ revision: 'file-revision' })
  expect(
    requests
      .slice(3)
      .every((request) => request.headers.get('X-API-Key') === 'root-key'),
  ).toBe(true)
})

it.each([
  ['{"port":${PORT},"enabled":$ENABLED,"nested":${OBJECT}}', true],
  [JSON.stringify({ port: '${PORT}', escaped: 'a"${KEY}' }), true],
  ['{"port":${PORT}} trailing', false],
  ['{"port":${PORT}', false],
  ['[${PORT}]', false],
  ['null', false],
])('checks startup-file object syntax for %s', async (content, valid) => {
  expect(isConfigFileObject(content)).toBe(valid)
})
it('waits for a different instance and tolerates temporary connection failures', async () => {
  const { waitForServerRestart } = await import('./config-file-api')
  vi.useFakeTimers()
  try {
    const status = vi
      .fn()
      .mockResolvedValueOnce({
        restart: { instance_id: 'old', restarting: false },
      })
      .mockRejectedValueOnce(new Error('connection refused'))
      .mockResolvedValueOnce({
        restart: { instance_id: 'new', restarting: false },
      })
    const wait = waitForServerRestart(
      status,
      'old',
      new AbortController().signal,
    )
    await vi.advanceTimersByTimeAsync(2000)
    await wait
    expect(status).toHaveBeenCalledTimes(3)
  } finally {
    vi.useRealTimers()
  }
})
it('does not report success when the original instance stays reachable', async () => {
  const { waitForServerRestart } = await import('./config-file-api')
  vi.useFakeTimers()
  try {
    const status = vi
      .fn()
      .mockResolvedValue({ restart: { instance_id: 'old', restarting: false } })
    const result = waitForServerRestart(
      status,
      'old',
      new AbortController().signal,
      2000,
    ).catch((error) => error)
    await vi.advanceTimersByTimeAsync(2000)
    expect((await result).message).toBe('Restart timed out')
  } finally {
    vi.useRealTimers()
  }
})

it('accepts a verbatim file draft with a UTF-8 BOM', () => {
  expect(isConfigFileObject('\ufeff{"server":{"port":${STUDIO_PORT}}}')).toBe(
    true,
  )
})
