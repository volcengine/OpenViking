// @vitest-environment jsdom
import { CompileList } from '#/features/workbench/compile'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchCompileTasks: vi.fn(async () => ({ items: [], next_cursor: null })),
  navigate: vi.fn(),
  search: { status: '', q: '' } as {
    status: string
    q: string
    tab?: 'schedules'
  },
}))
vi.mock('#/features/workbench/compile/lib/api', () => ({
  fetchCompileTasks: mocks.fetchCompileTasks,
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'tabs-test' }),
}))
vi.mock('@tanstack/react-router', () => ({
  getRouteApi: () => ({ useSearch: () => mocks.search }),
  useNavigate: () => mocks.navigate,
  useLocation: () => ({ state: { __TSR_index: 0 } }),
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}))
vi.mock('#/features/workbench/compile/components/capability-notice', () => ({
  useCompileCapability: () => ({}),
  CapabilityNotice: () => null,
}))
vi.mock('#/features/workbench/compile/components/context-link', () => ({
  useCompileSkillName: () => (uri: string) => uri,
  ContextLink: () => null,
}))
vi.mock('#/features/workbench/compile/components/shared', () => ({
  CompileShell: ({ children }: { children: ReactNode }) => (
    <main>{children}</main>
  ),
  CompileError: () => <div role="alert" />,
  CompileLoading: () => <div role="status" />,
  CompileStatus: () => null,
}))
vi.mock('#/features/workbench/compile/components/schedule-list', () => ({
  CompileScheduleList: () => <div>Schedule list</div>,
}))

let client: QueryClient

beforeEach(() => {
  vi.clearAllMocks()
  mocks.search = { status: '', q: '' }
})
afterEach(() => {
  cleanup()
  client.clear()
})
function mount() {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const content = () => (
    <QueryClientProvider client={client}>
      <CompileList />
    </QueryClientProvider>
  )
  const page = render(content())
  return { ...page, rerenderRoute: () => page.rerender(content()) }
}

it('switches tabs through route search and stops observing execution history while viewing plans', async () => {
  const page = mount()
  const tasksTab = page.getByRole('tab', { name: 'schedule.tasksTab' })
  const plansTab = page.getByRole('tab', { name: 'schedule.plansTab' })
  expect(tasksTab.getAttribute('aria-selected')).toBe('true')
  await page.findByText('empty')
  expect(page.queryByText('Schedule list')).toBeNull()
  fireEvent.click(plansTab)
  expect(mocks.navigate).toHaveBeenLastCalledWith({
    to: '/compile',
    search: { status: '', q: '', tab: 'schedules' },
  })
  mocks.search = { ...mocks.search, tab: 'schedules' }
  page.rerenderRoute()
  await page.findByText('Schedule list')
  expect(plansTab.getAttribute('aria-selected')).toBe('true')
  expect(page.queryByRole('textbox', { name: 'search' })).toBeNull()
  expect(
    client
      .getQueryCache()
      .find({ queryKey: ['compile-latest'], exact: false })!
      .getObserversCount(),
  ).toBe(0)
  fireEvent.click(tasksTab)
  expect(mocks.navigate).toHaveBeenLastCalledWith({
    to: '/compile',
    search: { status: '', q: '', tab: undefined },
  })
  mocks.search = { ...mocks.search, tab: undefined }
  page.rerenderRoute()
  await page.findByRole('textbox', { name: 'search' })
  expect(tasksTab.getAttribute('aria-selected')).toBe('true')
  expect(page.queryByText('Schedule list')).toBeNull()
})

it('filters execution history through the custom select while keeping the search text', async () => {
  mocks.search = { status: '', q: 'notes' }
  const page = mount()
  await userEvent.click(page.getByRole('combobox', { name: 'status' }))
  await userEvent.click(
    await screen.findByRole('option', { name: 'statuses.failed' }),
  )
  expect(mocks.navigate).toHaveBeenLastCalledWith({
    to: '/compile',
    search: { status: 'failed', q: 'notes' },
  })
  mocks.search = { status: 'failed', q: 'notes' }
  page.rerenderRoute()
  await userEvent.click(page.getByRole('combobox', { name: 'status' }))
  await userEvent.click(await screen.findByRole('option', { name: 'all' }))
  expect(mocks.navigate).toHaveBeenLastCalledWith({
    to: '/compile',
    search: { status: '', q: 'notes' },
  })
})

it('opens schedules from the saved-plan URL and preserves task filters when returning to history', async () => {
  mocks.search = { status: 'completed', q: 'notes', tab: 'schedules' }
  const page = mount()
  expect(page.getByText('Schedule list')).toBeTruthy()
  expect(
    page
      .getByRole('tab', { name: 'schedule.plansTab' })
      .getAttribute('aria-selected'),
  ).toBe('true')
  expect(mocks.fetchCompileTasks).not.toHaveBeenCalled()
  fireEvent.click(page.getByRole('tab', { name: 'schedule.tasksTab' }))
  expect(mocks.navigate).toHaveBeenCalledWith({
    to: '/compile',
    search: { status: 'completed', q: 'notes', tab: undefined },
  })
  mocks.search = { ...mocks.search, tab: undefined }
  page.rerenderRoute()
  await page.findByText('emptyFiltered')
  expect(
    (page.getByRole('textbox', { name: 'search' }) as HTMLInputElement).value,
  ).toBe('notes')
  expect(mocks.fetchCompileTasks).toHaveBeenCalledWith(
    'completed',
    'notes',
    undefined,
    expect.any(AbortSignal),
  )
})
