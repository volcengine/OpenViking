import { afterEach, expect, it, vi } from 'vitest'
import { supportsModelTest, testModelConnection } from './model-connection-test'

const state = vi.hoisted(() => ({ test: vi.fn() }))
vi.mock('./config-file-api', () => ({
  createConfigFileApi: () => ({ testModel: state.test }),
}))
afterEach(() => vi.resetAllMocks())
const connection = {
  baseUrl: 'http://server',
  adminApiKey: 'root',
  apiKey: 'user',
  accountId: 'default',
  userId: 'default',
}
const draft = {
  connection,
  trusted: false,
  kind: 'vlm' as const,
  config: { provider: 'volcengine', api_key: 'model-secret' },
  fallback: { model: 'inherited' },
  signal: new AbortController().signal,
}
it('delegates the unchanged draft to the private admin transport', async () => {
  state.test.mockResolvedValue({ ok: true, ms: 42 })
  expect(await testModelConnection(draft)).toBe(42)
  expect(state.test).toHaveBeenCalledWith(
    { kind: 'vlm', config: draft.config, fallback: draft.fallback },
    draft.signal,
  )
})
it('exposes only the structured failure code', async () => {
  state.test.mockResolvedValue({
    ok: false,
    code: 'authentication_error',
    message: 'sensitive server payload',
  })
  await expect(testModelConnection(draft)).rejects.toMatchObject({
    code: 'authentication_error',
  })
})

it.each([
  'jina',
  'gemini',
  'voyage',
  'dashscope',
  'minimax',
  'cohere',
  'litellm',
])('enables connection testing for the %s embedding adapter', (provider) =>
  expect(supportsModelTest('embedding', provider)).toBe(true),
)
