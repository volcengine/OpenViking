// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ContextExplorerHeader } from './context-explorer'

const connection = vi.hoisted(() => ({ serverMode: 'api_key' }))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'https://example.com/ov',
      apiKey: 'data-key',
      adminApiKey: 'admin-key',
    },
    identityScopeKey: 'test-user',
    serverMode: connection.serverMode,
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'zh-CN' },
  }),
}))
afterEach(() => {
  cleanup()
  connection.serverMode = 'api_key'
  vi.clearAllMocks()
})

it('keeps resource import available in trusted mode without offering API-key-only instructions', async () => {
  connection.serverMode = 'trusted'
  const onImport = vi.fn()
  render(<ContextExplorerHeader onAddResource={onImport} />)

  expect(screen.queryByRole('button', { name: 'ide.connectAgent' })).toBeNull()
  await userEvent.click(
    screen.getByRole('button', { name: 'explorer.addResource' }),
  )
  expect(onImport).toHaveBeenCalledOnce()
})

it('opens AgentConnect with the current data credential and closes inside Studio', async () => {
  const user = userEvent.setup()
  const openWindow = vi.spyOn(window, 'open')
  render(<ContextExplorerHeader />)
  await user.click(screen.getByRole('button', { name: 'ide.connectAgent' }))
  expect(screen.getByRole('dialog', { name: 'ide.connectAgent' })).toBeTruthy()
  expect(screen.getByRole('tab', { name: /主流/ })).toBeTruthy()
  expect(
    screen.getByText('https://example.com/ov', { exact: false }),
  ).toBeTruthy()
  expect(document.body.textContent).not.toContain('admin-key')
  await user.click(screen.getByRole('button', { name: '复制并发送给 Agent' }))
  const copied = await navigator.clipboard.readText()
  expect(copied).toContain('data-key')
  expect(copied).not.toContain('admin-key')
  expect(openWindow).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: '关闭' }))
  await vi.waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  openWindow.mockRestore()
})
