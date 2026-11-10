// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ContextExplorerHeader } from './context-explorer'

const { agentConnect } = vi.hoisted(() => ({
  agentConnect: vi.fn(() => <div>Agent setup</div>),
}))
vi.mock('@openviking/react/components/agent-connect', () => ({
  AgentConnect: agentConnect,
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'https://example.com/ov',
      apiKey: 'data-key',
      adminApiKey: 'admin-key',
    },
    identityScopeKey: 'test-user',
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
  vi.clearAllMocks()
})

it('opens AgentConnect with the current data credential and closes inside Studio', async () => {
  const user = userEvent.setup()
  const openWindow = vi.spyOn(window, 'open')
  render(
    <ContextExplorerHeader
      isRefreshing={false}
      onOpenSearch={vi.fn()}
      onRefresh={vi.fn()}
    />,
  )
  await user.click(screen.getByRole('button', { name: 'ide.connectAgent' }))
  expect(screen.getByRole('dialog', { name: 'ide.connectAgent' })).toBeTruthy()
  expect(agentConnect).toHaveBeenCalledWith(
    expect.objectContaining({
      apiKey: 'data-key',
      baseUrl: 'https://example.com/ov',
      locale: 'zh-CN',
    }),
    undefined,
  )
  expect(openWindow).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'ui.close' }))
  expect(screen.queryByRole('dialog')).toBeNull()
  openWindow.mockRestore()
})
