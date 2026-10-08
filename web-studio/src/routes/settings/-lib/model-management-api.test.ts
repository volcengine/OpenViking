import axios from 'axios'
import { afterEach, expect, it, vi } from 'vitest'
import {
  createModelManagementApi,
  embeddingPatch,
} from './model-management-api'
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
it('uses ROOT credentials, loads all effective models and saves each type in its proper endpoint', async () => {
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
              models: Object.fromEntries(
                ['vlm', 'embedding', 'query_planner', 'rerank'].map((kind) => [
                  kind,
                  { source: 'server', config: {} },
                ]),
              ),
            },
          },
        }
      },
    }),
  )
  const api = createModelManagementApi(
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
  await api.save(
    {
      vlm: { model: 'test' },
      query_planner: null,
      rerank: { model: 'test' },
      embedding: { max_retries: 2 },
    },
    loaded.revision,
  )
  expect(
    requests.every(
      (request) => request.headers.get('X-API-Key') === 'root-key',
    ),
  ).toBe(true)
  expect(requests[0].url).toContain('source=file')
  expect(requests[0].url).not.toContain('/accounts/')
  expect(JSON.parse(requests[1].data)).toEqual({
    revision: 'file-revision',
    settings: {
      vlm: { model: 'test' },
      query_planner: null,
      rerank: { model: 'test' },
      embedding: { max_retries: 2 },
    },
  })
  expect(requests).toHaveLength(2)
  expect(requests[1].method).toBe('patch')
  expect(requests[1].url).toContain('/admin/configuration?source=file')
})
