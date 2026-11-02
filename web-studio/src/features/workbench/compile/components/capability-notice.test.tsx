// @vitest-environment jsdom
import {
  CapabilityNotice,
  useCompileCapability,
} from '#/features/workbench/compile/components/capability-notice'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  fetchCapabilities: vi.fn(),
  openConnectionSettings: vi.fn(),
}))
vi.mock('#/features/workbench/compile/lib/api', () => ({
  fetchCapabilities: api.fetchCapabilities,
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    identityScopeKey: 'alice',
    openConnectionSettings: api.openConnectionSettings,
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}))
vi.mock('#/features/workbench/compile/components/shared', () => ({
  CompileError: ({ retry }: { retry: () => void }) => (
    <button onClick={retry}>Retry check</button>
  ),
}))
const clients: QueryClient[] = []
function Notice() {
  const query = useCompileCapability()
  return <CapabilityNotice capability={query} />
}
function mount() {
  const client = new QueryClient()
  clients.push(client)
  return render(
    <QueryClientProvider client={client}>
      <Notice />
    </QueryClientProvider>,
  )
}
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
  vi.resetAllMocks()
})

it('offers administrator guidance for unconfigured execution', async () => {
  api.fetchCapabilities.mockResolvedValue({
    can_create: false,
    reason_code: 'NOT_CONFIGURED',
  })
  const page = mount()
  expect(await page.findByText('capability.notConfigured')).toBeTruthy()
  expect(
    page
      .getByRole('link', { name: 'capability.adminGuide' })
      .getAttribute('href'),
  ).toContain('/en/api/23-agent-runtime')
  expect(page.queryByText('capability.keyRequired')).toBeNull()
})
it('opens connection settings when the identity needs an API key', async () => {
  api.fetchCapabilities.mockResolvedValue({
    can_create: false,
    reason_code: 'API_KEY_REQUIRED',
  })
  const page = mount()
  fireEvent.click(
    await page.findByRole('button', { name: 'capability.connection' }),
  )
  expect(api.openConnectionSettings).toHaveBeenCalledOnce()
})
it('retries failed checks without mislabeling them as unconfigured or healthy', async () => {
  api.fetchCapabilities
    .mockRejectedValueOnce(new Error('Offline'))
    .mockResolvedValue({ can_create: true })
  const page = mount()
  expect(await page.findByText('capability.failed')).toBeTruthy()
  expect(page.queryByText('capability.notConfigured')).toBeNull()
  fireEvent.click(page.getByText('Retry check'))
  await waitFor(() => expect(page.container.textContent).toBe(''))
})
