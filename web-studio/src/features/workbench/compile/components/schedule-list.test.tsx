// @vitest-environment jsdom
import { CompileScheduleList } from '#/features/workbench/compile/components/schedule-list'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'

const mocks = vi.hoisted(() => ({
  fetchCompileConfigs: vi.fn(),
  navigate: vi.fn(),
  identityScopeKey: 'account-a',
}))
vi.mock('#/features/workbench/compile/lib/api', () => ({
  fetchCompileConfigs: mocks.fetchCompileConfigs,
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: mocks.identityScopeKey }),
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { time?: string; weekday?: string }) =>
      [key, options?.weekday, options?.time].filter(Boolean).join(' '),
    i18n: { language: 'en' },
  }),
}))
vi.mock('#/features/workbench/compile/components/context-link', () => ({
  ContextLink: ({ uri, children }: { uri: string; children: ReactNode }) => (
    <a href={uri}>{children}</a>
  ),
}))
vi.mock('#/features/workbench/compile/components/shared', () => ({
  CompileLoading: () => <div role="status">Loading</div>,
  CompileError: ({ retry }: { retry: () => void }) => (
    <div role="alert">
      Could not load targets <button onClick={retry}>Retry</button>
    </div>
  ),
}))

const daily = {
  to: 'viking://resources/daily',
  status: 'ready',
  initialized: true,
  running: true,
  auto_update: true,
  update_schedule: { frequency: 'daily', hour: 8, minute: 5 },
  next_run_at: '2026-11-03T08:05:00+00:00',
}
const weekly = {
  ...daily,
  to: 'viking://resources/weekly',
  running: false,
  auto_update: false,
  update_schedule: { frequency: 'weekly', weekday: 3, hour: 14, minute: 30 },
  next_run_at: null,
}
const unscheduled = {
  ...weekly,
  to: 'viking://resources/unscheduled',
  update_schedule: null,
}
const clients: QueryClient[] = []
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  const content = () => (
    <QueryClientProvider client={client}>
      <CompileScheduleList />
    </QueryClientProvider>
  )
  const page = render(content())
  return { ...page, rerenderScope: () => page.rerender(content()) }
}
beforeEach(() => {
  vi.clearAllMocks()
  mocks.identityScopeKey = 'account-a'
})
afterEach(() => {
  cleanup()
  clients.forEach((client) => client.clear())
  clients.length = 0
})

it('shows all registered targets, schedule details, execution state, and editor actions', async () => {
  mocks.fetchCompileConfigs.mockResolvedValue({
    targets: [daily, weekly, unscheduled],
    next_cursor: null,
  })
  const page = mount()
  await page.findByRole('link', { name: daily.to })
  expect(page.getAllByRole('listitem')).toHaveLength(3)
  expect(page.getByText('schedule.dailyAt 08:05')).toBeTruthy()
  expect(
    page.getByText('schedule.weeklyAt schedule.weekdays.3 14:30'),
  ).toBeTruthy()
  expect(page.getByText('schedule.noSchedule')).toBeTruthy()
  expect(page.getByText('schedule.running')).toBeTruthy()
  expect(page.getAllByText('schedule.idle')).toHaveLength(2)
  expect(page.getAllByText('schedule.active')).toHaveLength(2)
  const nextRun = page.container.querySelector('time')!
  expect(nextRun.dateTime).toBe('2026-11-03T08:05:00.000Z')
  expect(nextRun.textContent).toBe(
    new Date(daily.next_run_at).toLocaleString('en', {
      timeZone: 'Asia/Shanghai',
    }),
  )
  fireEvent.click(
    page.getByRole('button', { name: `schedule.edit: ${weekly.to}` }),
  )
  expect(mocks.navigate).toHaveBeenCalledWith({
    to: '/compile/new',
    search: { target: weekly.to },
  })
  expect(mocks.fetchCompileConfigs).toHaveBeenCalledWith(
    undefined,
    expect.any(AbortSignal),
  )
})

it('filters loaded targets locally and keeps pagination available when a page has no matches', async () => {
  mocks.fetchCompileConfigs
    .mockResolvedValueOnce({ targets: [daily], next_cursor: 'page-2' })
    .mockResolvedValueOnce({ targets: [weekly], next_cursor: null })
  const page = mount()
  await page.findByRole('link', { name: daily.to })
  fireEvent.change(page.getByRole('combobox', { name: 'schedule.filter' }), {
    target: { value: 'disabled' },
  })
  expect(page.queryByRole('listitem')).toBeNull()
  expect(page.getByText('schedule.emptyLoaded')).toBeTruthy()
  expect(page.queryByText('schedule.emptyFiltered')).toBeNull()
  expect(mocks.fetchCompileConfigs).toHaveBeenCalledTimes(1)
  fireEvent.click(page.getByRole('button', { name: 'more' }))
  await page.findByRole('link', { name: weekly.to })
  expect(mocks.fetchCompileConfigs).toHaveBeenLastCalledWith(
    'page-2',
    expect.any(AbortSignal),
  )
  expect(page.queryByRole('link', { name: daily.to })).toBeNull()
  expect(page.queryByRole('button', { name: 'more' })).toBeNull()
  fireEvent.change(page.getByRole('combobox'), {
    target: { value: 'active' },
  })
  expect(page.getByRole('link', { name: daily.to })).toBeTruthy()
  expect(page.queryByRole('link', { name: weekly.to })).toBeNull()
  expect(mocks.fetchCompileConfigs).toHaveBeenCalledTimes(2)
})

it('only claims an exhaustive filtered empty result after the last page', async () => {
  mocks.fetchCompileConfigs.mockResolvedValue({
    targets: [daily],
    next_cursor: null,
  })
  const page = mount()
  await page.findByRole('link', { name: daily.to })
  fireEvent.change(page.getByRole('combobox'), {
    target: { value: 'disabled' },
  })
  expect(page.getByText('schedule.emptyFiltered')).toBeTruthy()
  expect(page.queryByText('schedule.emptyLoaded')).toBeNull()
})

it('shows loading and retryable errors, then the empty registered-target state', async () => {
  let rejectRequest: (error: Error) => void = () => {}
  mocks.fetchCompileConfigs.mockImplementationOnce(
    () => new Promise((_, reject) => (rejectRequest = reject)),
  )
  const page = mount()
  expect(page.getByRole('status')).toBeTruthy()
  rejectRequest(new Error('Unavailable'))
  await page.findByRole('alert')
  expect(page.queryByText('schedule.empty')).toBeNull()
  mocks.fetchCompileConfigs.mockResolvedValueOnce({
    targets: [],
    next_cursor: null,
  })
  fireEvent.click(page.getByRole('button', { name: 'Retry' }))
  await page.findByText('schedule.empty')
  expect(page.queryByRole('alert')).toBeNull()
})

it('refreshes the current list and isolates data when the active identity changes', async () => {
  mocks.fetchCompileConfigs
    .mockResolvedValueOnce({ targets: [daily], next_cursor: null })
    .mockResolvedValueOnce({ targets: [weekly], next_cursor: null })
    .mockResolvedValueOnce({ targets: [unscheduled], next_cursor: null })
  const page = mount()
  await page.findByRole('link', { name: daily.to })
  fireEvent.click(page.getByRole('button', { name: 'refresh' }))
  await page.findByRole('link', { name: weekly.to })
  expect(page.queryByRole('link', { name: daily.to })).toBeNull()
  mocks.identityScopeKey = 'account-b'
  page.rerenderScope()
  expect(page.queryByRole('link', { name: weekly.to })).toBeNull()
  await page.findByRole('link', { name: unscheduled.to })
  expect(mocks.fetchCompileConfigs).toHaveBeenCalledTimes(3)
})

it('retains loaded rows and allows retry after a later page fails', async () => {
  mocks.fetchCompileConfigs
    .mockResolvedValueOnce({ targets: [daily], next_cursor: 'page-2' })
    .mockRejectedValueOnce(new Error('Page unavailable'))
  const page = mount()
  await page.findByRole('link', { name: daily.to })
  fireEvent.click(page.getByRole('button', { name: 'more' }))
  await page.findByRole('alert')
  expect(page.getByRole('link', { name: daily.to })).toBeTruthy()
  expect(page.queryByText('schedule.empty')).toBeNull()
  mocks.fetchCompileConfigs.mockResolvedValueOnce({
    targets: [weekly],
    next_cursor: null,
  })
  fireEvent.click(page.getByRole('button', { name: 'more' }))
  await page.findByRole('link', { name: weekly.to })
  await waitFor(() => expect(page.queryByRole('alert')).toBeNull())
  expect(page.getByRole('link', { name: daily.to })).toBeTruthy()
})
