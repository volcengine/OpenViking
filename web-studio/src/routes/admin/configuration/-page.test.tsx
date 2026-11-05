// @vitest-environment jsdom
import { OpenVikingConfigurationPage } from '#/features/admin/account-configuration/page'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ role: 'admin' }))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: { accountId: 'team-a' },
    connectionRole: state.role,
    identityScopeKey: 'team-a-admin',
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('#/features/admin/account-configuration/experience-setting', () => ({
  EvolutionSettingRow: () => <span>experience setting</span>,
}))
afterEach(cleanup)
it.each(['root', 'admin'])(
  'provides a general configuration page with account settings for %s',
  (role) => {
    state.role = role
    render(<OpenVikingConfigurationPage />)
    expect(
      screen.getByRole('heading', { name: 'modes.configuration' }),
    ).toBeTruthy()
    expect(screen.getByText('experience setting')).toBeTruthy()
    expect(screen.queryByRole('tab')).toBeNull()
    expect(screen.queryByText('modes.serverConfiguration')).toBeNull()
    expect(screen.queryByText('studioSettings.theme')).toBeNull()
    expect(screen.queryByText('studioSettings.language')).toBeNull()
  },
)
