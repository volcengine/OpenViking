// @vitest-environment jsdom
import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import axios from 'axios'
import type { OpenVikingProviderProps } from '@openviking/react/client'
import { createOvClient } from '#/shared/api/ov-client'
import { StudioUiProvider } from './studio-ui-provider'

const m = vi.hoisted(() => ({
  scope: 'first',
  client: vi.fn(),
  provider: vi.fn<(props: OpenVikingProviderProps) => null>(() => null),
}))
vi.mock('@openviking/react/client', () => ({ OpenVikingProvider: m.provider }))
vi.mock('../hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: m.scope }),
}))
vi.mock('#/shared/api/ov-client', async (original) => ({
  ...(await original<object>()),
  getOvClient: m.client,
}))
const originalAdapter = axios.defaults.adapter
afterEach(() => {
  cleanup()
  axios.defaults.adapter = originalAdapter
  vi.clearAllMocks()
  m.scope = 'first'
})
it('uses the data credential and freezes each identity across later switches', async () => {
  const requests: Array<{ url?: string; key: unknown; account: unknown }> = []
  axios.defaults.adapter = async (config) => {
    requests.push({
      url: config.url,
      key: config.headers.get('X-API-Key'),
      account: config.headers.get('X-OpenViking-Account'),
    })
    return {
      status: 200,
      statusText: 'OK',
      config,
      headers: { 'content-type': 'application/json' },
      data: new TextEncoder().encode('{"status":"ok","result":[]}').buffer,
    }
  }
  const current = createOvClient({
    baseUrl: 'https://first.test',
    credentialScope: 'control',
    apiKeyStorageKey: '',
  })
  current.setConnection({
    apiKey: 'user-first',
    adminApiKey: 'admin-secret',
    accountId: 'one',
    userId: 'alice',
    identityHeaders: true,
  })
  m.client.mockReturnValue(current)
  const view = render(
    <StudioUiProvider>
      <div />
    </StudioUiProvider>,
  )
  const first = m.provider.mock.calls.at(-1)![0]
  current.setOptions({ baseUrl: 'https://second.test' })
  current.setConnection({
    apiKey: 'user-second',
    accountId: 'two',
    userId: 'bob',
  })
  m.scope = 'second'
  view.rerender(
    <StudioUiProvider>
      <div />
    </StudioUiProvider>,
  )
  const second = m.provider.mock.calls.at(-1)![0]
  await first.fetch!('https://first.test/api/v1/fs/ls')
  const response = await second.fetch!('https://second.test/api/v1/fs/ls')
  expect(await response.json()).toEqual({ status: 'ok', result: [] })
  expect(requests).toEqual([
    {
      url: 'https://first.test/api/v1/fs/ls',
      key: 'user-first',
      account: 'one',
    },
    {
      url: 'https://second.test/api/v1/fs/ls',
      key: 'user-second',
      account: 'two',
    },
  ])
})
