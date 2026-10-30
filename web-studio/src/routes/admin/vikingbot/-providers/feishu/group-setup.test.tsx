// @vitest-environment jsdom
import { verifyConnection } from '#/features/admin/vikingbot/api'
import { GroupSetup } from '#/features/admin/vikingbot/providers/feishu/group-setup'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

vi.mock('#/features/admin/vikingbot/api', () => ({ verifyConnection: vi.fn() }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(cleanup)

it('finishes published setup without a test or a verification mutation', () => {
  const close = vi.fn()
  render(
    <QueryClientProvider client={new QueryClient()}>
      <GroupSetup
        connection={{
          id: 'bot',
          app_id: 'app',
          bot_name: 'Bot',
          enabled: true,
          step: 4,
          revision: 1,
          identity_user: 'default',
          status: { state: 'connected' },
        }}
        onChange={vi.fn()}
        onClose={close}
      />
    </QueryClientProvider>,
  )
  expect(screen.getByText('setupComplete')).toBeTruthy()
  expect(screen.getByText('connectionHelp').closest('details')?.open).toBe(
    false,
  )
  fireEvent.click(screen.getByRole('button', { name: 'finish' }))
  expect(close).toHaveBeenCalledOnce()
  expect(verifyConnection).not.toHaveBeenCalled()
})
