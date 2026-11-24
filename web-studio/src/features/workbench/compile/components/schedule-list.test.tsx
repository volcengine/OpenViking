// @vitest-environment jsdom
import { CompileScheduleList } from '#/features/workbench/compile/components/schedule-list'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'

const mocks = vi.hoisted(() => ({
  fetchCompileConfigs: vi.fn(),
  fetchCompileConfig: vi.fn(),
  updateCompileConfig: vi.fn(),
  success: vi.fn(),
  navigate: vi.fn(),
  identityScopeKey: 'account-a',
}))
vi.mock('#/features/workbench/compile/lib/api', () => ({
  fetchCompileConfigs: mocks.fetchCompileConfigs,
  fetchCompileConfig: mocks.fetchCompileConfig,
  updateCompileConfig: mocks.updateCompileConfig,
}))
vi.mock('sonner', () => ({ toast: { success: mocks.success } }))
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
async function choose(trigger: HTMLElement, name: string) {
  await userEvent.click(trigger)
  await userEvent.click(await screen.findByRole('option', { name }))
  await waitFor(() =>
    expect(trigger.getAttribute('aria-expanded')).toBe('false'),
  )
}

const clients: QueryClient[] = []
function mount(cachedConfig?: unknown) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  if (cachedConfig)
    client.setQueryData(['compile-config', 'account-a', daily.to], cachedConfig)
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
  mocks.fetchCompileConfig.mockReset()
  mocks.updateCompileConfig.mockReset()
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
  expect(page.getAllByText('schedule.active')).toHaveLength(1)
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
  await choose(
    page.getByRole('combobox', { name: 'schedule.filter' }),
    'schedule.disabled',
  )
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
  await choose(page.getByRole('combobox'), 'schedule.active')
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
  await choose(page.getByRole('combobox'), 'schedule.disabled')
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

const freshConfig = {
  ...daily,
  from: ['viking://resources/fresh-source'],
  skill: 'viking://agent/skills/fresh-skill',
  instruction: 'Keep current instructions',
  last_error: null,
  last_run: null,
}

it('turns off directly from the list, preserves fresh compilation settings, and blocks duplicate clicks', async () => {
  mocks.fetchCompileConfigs.mockResolvedValue({
    targets: [daily],
    next_cursor: null,
  })
  mocks.fetchCompileConfig.mockResolvedValue(freshConfig)
  let finish: (value: unknown) => void = () => {}
  mocks.updateCompileConfig.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  )
  const page = mount()
  const toggle = await page.findByRole('switch', {
    name: `schedule.autoUpdate: ${daily.to}`,
  })
  fireEvent.click(toggle)
  await waitFor(() =>
    expect(mocks.updateCompileConfig).toHaveBeenCalledWith({
      to: daily.to,
      from: freshConfig.from,
      skill: freshConfig.skill,
      instruction: freshConfig.instruction,
      auto_update: false,
      update_schedule: null,
    }),
  )
  expect(toggle.getAttribute('aria-disabled')).toBe('true')
  expect(toggle.getAttribute('aria-checked')).toBe('true')
  expect(page.getByText('schedule.saving')).toBeTruthy()
  fireEvent.click(toggle)
  expect(mocks.updateCompileConfig).toHaveBeenCalledTimes(1)
  const disabled = {
    ...freshConfig,
    auto_update: false,
    update_schedule: null,
    next_run_at: null,
  }
  mocks.fetchCompileConfigs.mockResolvedValue({
    targets: [disabled],
    next_cursor: null,
  })
  finish(disabled)
  await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('false'))
  expect(page.getByText('schedule.noSchedule')).toBeTruthy()
  expect(mocks.success).toHaveBeenCalledWith('schedule.turnedOff')
  expect(mocks.navigate).not.toHaveBeenCalled()
})

it('keeps the switch on when disabling fails and allows retry', async () => {
  mocks.fetchCompileConfigs.mockResolvedValue({
    targets: [daily],
    next_cursor: null,
  })
  mocks.fetchCompileConfig.mockResolvedValue(freshConfig)
  mocks.updateCompileConfig.mockRejectedValueOnce(
    new Error('Redis unavailable'),
  )
  const page = mount()
  const toggle = await page.findByRole('switch')
  fireEvent.click(toggle)
  await page.findByRole('alert')
  expect(toggle.getAttribute('aria-checked')).toBe('true')
  expect(mocks.success).not.toHaveBeenCalled()
  const disabled = {
    ...freshConfig,
    auto_update: false,
    update_schedule: null,
    next_run_at: null,
  }
  mocks.updateCompileConfig.mockResolvedValueOnce(disabled)
  mocks.fetchCompileConfigs.mockResolvedValue({
    targets: [disabled],
    next_cursor: null,
  })
  fireEvent.click(page.getByRole('button', { name: 'Retry' }))
  await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('false'))
  expect(page.queryByRole('alert')).toBeNull()
})

it('opens time settings before enabling and cancel leaves the plan disabled', async () => {
  mocks.fetchCompileConfigs.mockResolvedValue({
    targets: [unscheduled],
    next_cursor: null,
  })
  mocks.fetchCompileConfig.mockResolvedValue({ ...freshConfig, ...unscheduled })
  const page = mount()
  const toggle = await page.findByRole('switch')
  fireEvent.click(toggle)
  const dialog = within(await page.findByRole('dialog'))
  await dialog.findByRole('combobox', { name: 'schedule.hour' })
  expect(toggle.getAttribute('aria-checked')).toBe('false')
  expect(mocks.updateCompileConfig).not.toHaveBeenCalled()
  fireEvent.click(dialog.getByRole('button', { name: 'cancel' }))
  await waitFor(() => expect(page.queryByRole('dialog')).toBeNull())
  expect(toggle.getAttribute('aria-checked')).toBe('false')
  expect(mocks.updateCompileConfig).not.toHaveBeenCalled()
})

it('enables a weekly plan in place and refetches current source settings before saving', async () => {
  mocks.fetchCompileConfigs.mockResolvedValue({
    targets: [unscheduled],
    next_cursor: null,
  })
  mocks.fetchCompileConfig
    .mockResolvedValueOnce({ ...freshConfig, ...unscheduled })
    .mockResolvedValueOnce(freshConfig)
  const enabled = {
    ...freshConfig,
    to: unscheduled.to,
    auto_update: true,
    update_schedule: { frequency: 'weekly', weekday: 7, hour: 0, minute: 5 },
  }
  mocks.updateCompileConfig.mockImplementationOnce(async () => {
    mocks.fetchCompileConfigs.mockResolvedValue({
      targets: [enabled],
      next_cursor: null,
    })
    return enabled
  })
  const page = mount()
  const toggle = await page.findByRole('switch')
  fireEvent.click(toggle)
  const dialog = within(await page.findByRole('dialog'))
  await dialog.findByRole('combobox', { name: 'schedule.frequency' })
  await choose(
    dialog.getByRole('combobox', { name: 'schedule.frequency' }),
    'schedule.weekly',
  )
  await choose(
    dialog.getByRole('combobox', { name: 'schedule.weekday' }),
    'schedule.weekdays.7',
  )
  await choose(dialog.getByRole('combobox', { name: 'schedule.hour' }), '00')
  await choose(dialog.getByRole('combobox', { name: 'schedule.minute' }), '05')
  fireEvent.click(dialog.getByRole('button', { name: 'schedule.turnOn' }))
  await waitFor(() =>
    expect(mocks.updateCompileConfig).toHaveBeenCalledWith({
      to: unscheduled.to,
      from: freshConfig.from,
      skill: freshConfig.skill,
      instruction: freshConfig.instruction,
      auto_update: true,
      update_schedule: enabled.update_schedule,
    }),
  )
  await waitFor(() => expect(page.queryByRole('dialog')).toBeNull())
  expect(toggle.getAttribute('aria-checked')).toBe('true')
  expect(
    page.getByText('schedule.weeklyAt schedule.weekdays.7 00:05'),
  ).toBeTruthy()
  expect(mocks.navigate).not.toHaveBeenCalled()
})

it('waits for fresh settings when adjusting a cached plan and retains edits after a save failure', async () => {
  mocks.fetchCompileConfigs.mockResolvedValue({
    targets: [daily],
    next_cursor: null,
  })
  let load: (value: unknown) => void = () => {}
  mocks.fetchCompileConfig.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        load = resolve
      }),
  )
  mocks.fetchCompileConfig.mockResolvedValue(freshConfig)
  mocks.updateCompileConfig.mockRejectedValueOnce(new Error('Save failed'))
  const page = mount(freshConfig)
  fireEvent.click(
    await page.findByRole('button', { name: `schedule.adjust: ${daily.to}` }),
  )
  const dialog = within(await page.findByRole('dialog'))
  expect(
    dialog
      .getByRole('button', { name: 'schedule.save' })
      .hasAttribute('disabled'),
  ).toBe(true)
  expect(dialog.queryByRole('combobox')).toBeNull()
  load({
    ...freshConfig,
    update_schedule: { frequency: 'daily', hour: 23, minute: 45 },
  })
  const hour = await dialog.findByRole('combobox', { name: 'schedule.hour' })
  expect(hour.querySelector('[data-slot="select-value"]')?.textContent).toBe(
    '23',
  )
  await choose(hour, '14')
  fireEvent.click(dialog.getByRole('button', { name: 'schedule.save' }))
  await dialog.findByRole('alert')
  expect(hour.querySelector('[data-slot="select-value"]')?.textContent).toBe(
    '14',
  )
  expect(
    dialog
      .getByRole('button', { name: 'schedule.save' })
      .hasAttribute('disabled'),
  ).toBe(false)
  expect(
    page.getByRole('switch', { hidden: true }).getAttribute('aria-checked'),
  ).toBe('true')
  // Closing a failed enable/edit must not expose a misleading "disable" retry.
  fireEvent.click(dialog.getByRole('button', { name: 'cancel' }))
  await waitFor(() => expect(page.queryByRole('dialog')).toBeNull())
  expect(page.queryByRole('alert')).toBeNull()
})

it('retries loading schedule settings without allowing a save from stale data', async () => {
  mocks.fetchCompileConfigs.mockResolvedValue({
    targets: [daily],
    next_cursor: null,
  })
  mocks.fetchCompileConfig.mockRejectedValueOnce(new Error('Unavailable'))
  const page = mount(freshConfig)
  fireEvent.click(
    await page.findByRole('button', { name: `schedule.adjust: ${daily.to}` }),
  )
  const dialog = within(await page.findByRole('dialog'))
  await dialog.findByRole('alert')
  expect(
    dialog
      .getByRole('button', { name: 'schedule.save' })
      .hasAttribute('disabled'),
  ).toBe(true)
  mocks.fetchCompileConfig.mockResolvedValue(freshConfig)
  fireEvent.click(dialog.getByRole('button', { name: 'Retry' }))
  await dialog.findByRole('combobox', { name: 'schedule.hour' })
  expect(
    dialog
      .getByRole('button', { name: 'schedule.save' })
      .hasAttribute('disabled'),
  ).toBe(false)
})

it('removes a disabled plan from the enabled filter after the server confirms the update', async () => {
  mocks.fetchCompileConfigs.mockResolvedValue({
    targets: [daily],
    next_cursor: null,
  })
  mocks.fetchCompileConfig.mockResolvedValue(freshConfig)
  const disabled = {
    ...freshConfig,
    auto_update: false,
    update_schedule: null,
    next_run_at: null,
  }
  mocks.updateCompileConfig.mockImplementationOnce(async () => {
    mocks.fetchCompileConfigs.mockResolvedValue({
      targets: [disabled],
      next_cursor: null,
    })
    return disabled
  })
  const page = mount()
  await page.findByRole('switch')
  await choose(page.getByRole('combobox'), 'schedule.active')
  fireEvent.click(page.getByRole('switch'))
  await page.findByText('schedule.emptyFiltered')
  await choose(page.getByRole('combobox'), 'schedule.disabled')
  expect(page.getByRole('switch').getAttribute('aria-checked')).toBe('false')
})

it('does not submit settings under a new identity after switching connections during a read', async () => {
  mocks.fetchCompileConfigs
    .mockResolvedValueOnce({ targets: [daily], next_cursor: null })
    .mockResolvedValueOnce({ targets: [daily], next_cursor: null })
  let finish: (value: unknown) => void = () => {}
  mocks.fetchCompileConfig.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve
      }),
  )
  const page = mount()
  fireEvent.click(await page.findByRole('switch'))
  await waitFor(() => expect(mocks.fetchCompileConfig).toHaveBeenCalled())
  const signal = mocks.fetchCompileConfig.mock.calls[0][1] as AbortSignal
  mocks.identityScopeKey = 'account-b'
  page.rerenderScope()
  await page.findByRole('switch')
  expect(signal.aborted).toBe(true)
  finish(freshConfig)
  await waitFor(() => expect(clients[0].isMutating()).toBe(0))
  expect(mocks.updateCompileConfig).not.toHaveBeenCalled()
  expect(page.getByRole('switch').getAttribute('aria-checked')).toBe('true')
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
