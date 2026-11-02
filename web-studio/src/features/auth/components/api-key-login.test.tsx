// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createServer } from 'node:http'
import { once } from 'node:events'
import i18n from '#/i18n'
import { ApiKeyLogin } from './api-key-login'
import {
  getStudioExperience,
  resolveStudioExperience,
} from '#/platform/deployment/experience'
import { resolveStudioDeployment } from '#/platform/deployment/config'

// Preserve the real HTTP client and onboarding validator. Only storage is
// replaced; encryption and persistence failures have their own existing tests.
vi.mock('#/features/connection/lib/credential-storage', () => ({
  readCredentialStorage: (key: string) => localStorage.getItem(key),
  writeCredentialStorage: (key: string, value: string) =>
    localStorage.setItem(key, value),
  flushCredentialStorage: async () => {},
}))
beforeEach(async () => {
  localStorage.clear()
  sessionStorage.clear()
  await i18n.changeLanguage('en')
})
afterEach(() => {
  cleanup()
  delete window.__OV_STUDIO_CONFIG__
})

it.each(['root', 'admin', 'user'] as const)(
  'logs in with one %s Key and verifies only the proper API scope',
  async (role) => {
    const observed: Array<{ path: string; key: string }> = []
    const server = createServer((req, res) => {
      res.setHeader('Access-Control-Allow-Origin', '*')
      res.setHeader('Access-Control-Allow-Headers', '*')
      if (req.method === 'OPTIONS') return void res.end()
      const path = req.url || ''
      observed.push({ path, key: String(req.headers['x-api-key'] || '') })
      res.setHeader('Content-Type', 'application/json')
      res.end(
        JSON.stringify(
          path === '/health'
            ? {
                version: 'test',
                auth_mode: 'api_key',
                role,
                account_id: 'team',
                user_id: role === 'root' ? 'default' : 'alice',
              }
            : { status: 'ok', result: [] },
        ),
      )
    })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const url = `http://127.0.0.1:${(server.address() as { port: number }).port}`
    const complete = vi.fn()
    try {
      render(
        <ApiKeyLogin
          config={getStudioExperience().auth.apiKey}
          onComplete={complete}
        />,
      )
      fireEvent.change(screen.getByLabelText('Service Address'), {
        target: { value: url },
      })
      fireEvent.change(screen.getByLabelText('API Key'), {
        target: { value: ' synthetic- key ' },
      })
      fireEvent.click(screen.getByRole('button', { name: 'Sign in' }))
      await waitFor(() => expect(complete).toHaveBeenCalledOnce())
      const result = complete.mock.calls[0][0]
      const connection = result.verified.configuration
      expect(result.destination).toBe(role === 'root' ? '/admin' : '/home')
      expect(connection.apiKey).toBe(role === 'root' ? '' : 'synthetic-key')
      expect(connection.adminApiKey).toBe(
        role === 'user' ? '' : 'synthetic-key',
      )
      expect(connection.userId).toBe(role === 'root' ? '' : 'alice')
      expect(observed.every((r) => r.key === 'synthetic-key')).toBe(true)
      expect(
        observed.some((r) => r.path.startsWith('/api/v1/admin/accounts')),
      ).toBe(role === 'root')
      expect(observed.some((r) => r.path.startsWith('/api/v1/fs/ls'))).toBe(
        role !== 'root',
      )
      expect(
        JSON.parse(localStorage.getItem('ov_console_connection_profiles')!)
          .profiles,
      ).toHaveLength(1)
    } finally {
      cleanup()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  },
)

it('official deployment fixes the hosted endpoint and omits private service selection', () => {
  window.__OV_STUDIO_CONFIG__ = {
    edition: 'volcengine',
    hostedApiBaseUrl: 'https://cloud.example/ov',
  }
  render(
    <ApiKeyLogin
      config={getStudioExperience().auth.apiKey}
      onComplete={vi.fn()}
    />,
  )
  expect(screen.queryByRole('radio')).toBeNull()
  expect(
    screen.getByLabelText<HTMLInputElement>('Service Address').readOnly,
  ).toBe(true)
  expect(screen.getByLabelText<HTMLInputElement>('Service Address').value).toBe(
    'https://cloud.example/ov',
  )
})

it('renders the injected entry configuration without reading global deployment or location', () => {
  const config = resolveStudioExperience(resolveStudioDeployment()).auth.apiKey
  window.__OV_STUDIO_CONFIG__ = { edition: 'volcengine' }
  render(
    <ApiKeyLogin
      config={config}
      initialAddress="https://private.example"
      onComplete={vi.fn()}
    />,
  )
  expect(screen.getByRole('radio', { name: 'Self-hosted' })).toBeTruthy()
  expect(screen.getByLabelText<HTMLInputElement>('Service Address').value).toBe(
    'https://private.example',
  )
  fireEvent.change(screen.getByLabelText('API Key'), {
    target: { value: 'synthetic-unsent-key' },
  })
  fireEvent.click(screen.getByRole('radio', { name: 'Volcengine hosted' }))
  expect(
    screen.getByLabelText<HTMLInputElement>('Service Address').readOnly,
  ).toBe(true)
  expect(screen.getByLabelText<HTMLInputElement>('API Key').value).toBe('')
})
