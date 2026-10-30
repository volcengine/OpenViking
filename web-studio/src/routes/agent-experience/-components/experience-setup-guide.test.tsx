// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import en from '#/i18n/locales/en/workspace'
import zh from '#/i18n/locales/zh-CN/workspace'
import { ExperienceSetupGuide } from './experience-setup-guide'

const api = vi.hoisted(() => ({
  fetchStatus: vi.fn(),
  setEnabled: vi.fn(),
}))
const connection = vi.hoisted(() => ({ serverMode: 'dev' }))

vi.mock('../-lib/api', () => ({
  fetchAgentEvolutionStatus: api.fetchStatus,
  setAgentEvolutionEnabled: api.setEnabled,
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      accountId: 'acme',
      adminApiKey: '',
      apiKey: '',
      baseUrl: 'http://localhost:1933',
      userId: 'default',
    },
    connectionRole: 'root',
    identityScopeKey: 'test-scope',
    isConnectionRoleLoading: false,
    serverMode: connection.serverMode,
  }),
}))

afterEach(cleanup)
beforeEach(() => {
  vi.clearAllMocks()
  connection.serverMode = 'dev'
})

async function renderGuide(language: 'en' | 'zh-CN') {
  const i18n = createInstance()
  await i18n.init({
    fallbackLng: false,
    interpolation: { escapeValue: false },
    lng: language,
    resources: { en, 'zh-CN': zh },
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        <ExperienceSetupGuide />
      </I18nextProvider>
    </QueryClientProvider>,
  )
  return queryClient
}

describe('ExperienceSetupGuide Agent Evolution setup', () => {
  it.each([
    [
      'en',
      'Expand steps',
      'Agent Evolution is controlled by server configuration',
      'Experience settings',
    ],
    ['zh-CN', '展开步骤', 'Agent Evolution 由服务端配置控制', '经验设置'],
  ] as const)(
    'explains configuration-managed evolution in dev mode (%s)',
    async (language, expandLabel, expectedNotice, settingsLabel) => {
      await renderGuide(language)

      fireEvent.click(screen.getByRole('button', { name: expandLabel }))

      expect(screen.getByText(expectedNotice)).toBeTruthy()
      expect(screen.getByText(/server\.agent_evolution\.enabled/)).toBeTruthy()
      expect(screen.queryByRole('button', { name: settingsLabel })).toBeNull()
      expect(api.fetchStatus).not.toHaveBeenCalled()
      expect(api.setEnabled).not.toHaveBeenCalled()
    },
  )

  it('keeps account settings available outside dev mode', async () => {
    connection.serverMode = 'api_key'
    api.fetchStatus.mockResolvedValue({ accountId: 'acme', enabled: true })
    const queryClient = await renderGuide('en')
    fireEvent.click(screen.getByRole('button', { name: 'Expand steps' }))

    await waitFor(() => expect(api.fetchStatus).toHaveBeenCalled())
    expect(
      screen.getByRole('button', { name: 'Experience settings' }),
    ).toBeTruthy()
    expect(screen.getByText('Enabled')).toBeTruthy()

    queryClient.clear()
  })
})
