// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { MonitoringRoute } from './page'
import { NAV_ITEMS } from '#/app/navigation/workbench-navigation'
import { featureForPath } from '#/shared/lib/studio-service'

const api = vi.hoisted(() => ({ observer: vi.fn() }))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'root', serverMode: 'normal' }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { resolvedLanguage: 'en' },
    t: (key: string) => key,
  }),
}))
vi.mock('#/shared/api/ov-client/index', () => ({
  getHealth: () => Promise.resolve({ version: 'v1' }),
  getObserverSystem: api.observer,
  getOvResult: (value: unknown) => value,
}))
vi.mock(
  '#/features/workbench/monitoring/components/observer-status-content',
  () => ({
    ObserverStatusContent: ({ status }: { status: string }) => (
      <pre>{status}</pre>
    ),
  }),
)
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}))
afterEach(cleanup)
function mount() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MonitoringRoute />
    </QueryClientProvider>,
  )
}
it('places monitoring in management and removes the workbench destination', () => {
  expect(NAV_ITEMS.some((item) => String(item.to) === '/monitoring')).toBe(
    false,
  )
  expect(featureForPath('/admin/monitoring')?.domain).toBe('management')
})
it('shows actual component health and opens its matching detail from a card', async () => {
  api.observer.mockResolvedValue({
    is_healthy: false,
    components: {
      queue: { is_healthy: false, has_errors: true, status: 'queue failure' },
      models: { is_healthy: true, has_errors: false, status: 'models ready' },
    },
  })
  mount()
  await screen.findByText('summary.unhealthy')
  expect(screen.getAllByText('health.unknown')).toHaveLength(4)
  fireEvent.click(screen.getByRole('button', { name: /tabs.queue/ }))
  expect(
    screen
      .getByRole('tab', { name: 'tabs.queue' })
      .getAttribute('aria-selected'),
  ).toBe('true')
  expect(screen.getByRole('tabpanel').textContent).toContain('queue failure')
  fireEvent.click(screen.getByRole('tab', { name: 'tabs.overview' }))
  expect(
    screen.getAllByRole('button', { name: /detail.descriptions/ }),
  ).toHaveLength(6)
})
it('keeps a failed observer request visible instead of reporting healthy components', async () => {
  api.observer.mockRejectedValue(new Error('observer unavailable'))
  mount()
  await screen.findByText('observer unavailable')
  expect(screen.queryByText('summary.healthy')).toBeNull()
})
