// @vitest-environment jsdom
import { createServer } from 'node:http'
import { once } from 'node:events'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { expect, it, vi } from 'vitest'
import { useStudioCapabilities } from './use-studio-capabilities'
import { ovClient } from '#/lib/ov-client'

const state = vi.hoisted(() => ({ url: '' }))
vi.mock('./use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: state.url,
      apiKey: 'scoped-data-key',
      adminApiKey: 'control-key',
    },
    identityScopeKey: 'snapshot-scope',
  }),
}))
vi.mock('./use-studio-service', () => ({
  useStudioService: () => ({ ready: true, provider: 'volcengine' }),
}))

it('uses the query credential snapshot and verifies contracts even when gateway errors use HTTP 200', async () => {
  const requests: Array<{ path: string; key: unknown }> = []
  const server = createServer((req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*')
    res.setHeader('Access-Control-Allow-Headers', '*')
    if (req.method === 'OPTIONS') {
      res.end()
      return
    }
    requests.push({ path: req.url!, key: req.headers['x-api-key'] })
    res.setHeader('Content-Type', 'application/json')
    res.end(
      JSON.stringify(
        req.url === '/api/v1/watches'
          ? { status: 'ok', result: { tasks: [] } }
          : req.url === '/api/v1/console/audit'
            ? { ResponseMetadata: { Error: { Code: 'ApiBlocked' } } }
            : { message: 'Generic proxy JSON' },
      ),
    )
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  state.url = `http://127.0.0.1:${(server.address() as { port: number }).port}`
  const originalConnection = ovClient.getConnection()
  ovClient.setConnection({ apiKey: 'another-connections-key' })
  const client = new QueryClient()
  try {
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    )
    const { result } = renderHook(useStudioCapabilities, { wrapper })
    await waitFor(() => expect(result.current.monitoring?.isSuccess).toBe(true))
    expect(result.current.watches?.data).toBe('supported')
    expect(result.current.requestLogs?.data).toBe('unavailable')
    expect(result.current.monitoring?.data).toBe('unknown')
    expect(requests).toHaveLength(3)
    expect(requests.every((request) => request.key === 'scoped-data-key')).toBe(
      true,
    )
    expect(requests.some((request) => /compile|bot/.test(request.path))).toBe(
      false,
    )
  } finally {
    cleanup()
    client.clear()
    ovClient.setConnection(originalConnection)
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})
