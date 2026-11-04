// @vitest-environment jsdom
import { waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ConnectionController } from './connection-controller'

const health = vi.hoisted(() => vi.fn())
vi.mock('./health', async (original) => ({
  ...(await original<object>()),
  fetchServerHealth: health,
}))
vi.mock('./credential-storage', () => ({
  readCredentialStorage: (key: string) => localStorage.getItem(key),
  writeCredentialStorage: (key: string, value: string) =>
    localStorage.setItem(key, value),
}))
afterEach(() => {
  health.mockReset()
  localStorage.clear()
  sessionStorage.clear()
})

it('cancels a stopped lifecycle, rejects obsolete commands and can restart without React', async () => {
  const connection = {
    baseUrl: 'https://ov.example',
    serviceSelection: 'opensource' as const,
    accountId: 'team',
    userId: 'alice',
    apiKey: 'alice-key',
    adminApiKey: '',
  }
  localStorage.setItem('ov_console_connection', JSON.stringify(connection))
  let resolveOld!: (value: unknown) => void
  let oldSignal!: AbortSignal
  health
    .mockImplementationOnce((_url, _headers, signal) => {
      oldSignal = signal
      return new Promise((resolve) => {
        resolveOld = resolve
      })
    })
    .mockResolvedValue({
      status: 'ok',
      auth_mode: 'api_key',
      version: 'current',
      role: 'user',
      account_id: 'team',
      user_id: 'alice',
    })
  const clearCache = vi.fn()
  const navigate = vi.fn()
  const controller = new ConnectionController('/home', { clearCache, navigate })
  const stop = controller.start()
  const obsoleteSave = controller.getSnapshot().saveConnection
  stop()
  expect(oldSignal.aborted).toBe(true)
  expect(() => obsoleteSave({ ...connection, apiKey: 'old-command' })).toThrow()
  const stopRestart = controller.start()
  try {
    await waitFor(() =>
      expect(controller.getSnapshot().connectionRole).toBe('user'),
    )
    resolveOld({ auth_mode: 'trusted', role: 'root', version: 'obsolete' })
    await Promise.resolve()
    expect(controller.getSnapshot().serverMode).toBe('api_key')
    expect(controller.getSnapshot().serverHealth?.version).toBe('current')
    expect(controller.getSnapshot().connection.apiKey).toBe('alice-key')
    expect(navigate).not.toHaveBeenCalled()
  } finally {
    stopRestart()
  }
})
