// @vitest-environment jsdom
import { queryClient as studioQueryClient } from '#/app/providers/query-client'
import { CompileForm } from '#/features/workbench/compile/components/compile-form'
import { prepareCompileHandoff } from '#/features/workbench/compile/lib/handoff'
import { OvClientError } from '#/shared/api/ov-client/index'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import userEvent from '@testing-library/user-event'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  BUILTIN_SKILLS_ROOT: 'viking://system/compile/example-skills',
  createCompile: vi.fn(),
  fetchCompileConfig: vi.fn(),
  updateCompileConfig: vi.fn(),
  lookupSubmission: vi.fn(),
  fetchCompileSkills: vi.fn(async () => []),
  fetchCompileTask: vi.fn(),
  fetchCapabilities: vi.fn(async () => ({
    configured: true,
    can_create: true,
  })),
  navigate: vi.fn(),
}))
vi.mock('#/features/workbench/compile/lib/api', () => api)
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => api.navigate,
  useBlocker: () => {},
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    identityScopeKey: 'recovery-test',
    connection: { userId: 'alice' },
  }),
}))
vi.mock('#/features/workbench/compile/components/shared', () => ({
  CompileShell: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  CompileError: ({ retry }: { retry?: () => void }) => (
    <div role="alert">
      Submission error
      {retry && <button onClick={retry}>Retry read</button>}
    </div>
  ),
}))
vi.mock('#/features/workbench/compile/components/skill-picker', () => ({
  SkillPicker: () => null,
}))
vi.mock(
  '#/features/workbench/resources/components/directory-picker-dialog',
  () => ({
    DirectoryPickerDialog: () => null,
  }),
)
vi.mock('#/features/workbench/resources/lib/api', () => ({
  fetchFileContent: vi.fn(),
  fetchFsStat: vi.fn(async () => ({ isDir: true })),
}))

async function choose(trigger: HTMLElement, name: string) {
  await userEvent.click(trigger)
  await userEvent.click(await screen.findByRole('option', { name }))
  await waitFor(() =>
    expect(trigger.getAttribute('aria-expanded')).toBe('false'),
  )
}

const storageKey = 'compile-submission:recovery-test'
const clients: QueryClient[] = []
const task = { task_id: 'cmp-original', status: 'pending' }
function mount(
  props: { fromTask?: string; sourceUri?: string; target?: string } = {},
  cachedConfig?: unknown,
  cachedAt = Date.now(),
) {
  const client = new QueryClient({
    defaultOptions: { queries: studioQueryClient.getDefaultOptions().queries },
  })
  clients.push(client)
  if (cachedConfig)
    client.setQueryData(
      ['compile-config', 'recovery-test', props.target],
      cachedConfig,
      { updatedAt: cachedAt },
    )
  const page = render(
    <QueryClientProvider client={client}>
      <CompileForm {...props} />
    </QueryClientProvider>,
  )
  return {
    ...page,
    submit: () => fireEvent.submit(page.container.querySelector('form')!),
  }
}
beforeEach(() => {
  vi.clearAllMocks()
  api.fetchCapabilities.mockResolvedValue({
    configured: true,
    can_create: true,
  })
  localStorage.clear()
  sessionStorage.clear()
})

const savedDraft = {
  skill: 'viking://agent/skills/wiki',
  sources:
    'viking://resources/one\nviking://resources/two\nviking://resources/three',
  to: 'viking://resources/wiki',
  instruction: 'Keep citations',
}
const draftKey = 'compile-draft:recovery-test'

it('prefills only the current user resource root and preserves the old draft until explicitly restored', () => {
  localStorage.setItem(draftKey, JSON.stringify(savedDraft))
  const page = mount()
  const fields = page.getAllByRole('textbox', { name: 'sourceNumber' })
  expect(fields).toHaveLength(1)
  expect((fields[0] as HTMLInputElement).value).toBe(
    'viking://user/alice/resources',
  )
  expect(JSON.parse(localStorage.getItem(draftKey)!)).toEqual(savedDraft)
  fireEvent.click(page.getByRole('button', { name: 'restoreDraft' }))
  expect(
    page
      .getAllByRole('textbox', { name: 'sourceNumber' })
      .map((field) => (field as HTMLInputElement).value),
  ).toEqual(savedDraft.sources.split('\n'))
  expect(
    (page.getByRole('textbox', { name: 'target *' }) as HTMLInputElement).value,
  ).toBe(savedDraft.to)
  expect(page.queryByRole('button', { name: 'restoreDraft' })).toBeNull()
})

it('starts a new draft on editing and keeps clear from restoring previous sources', () => {
  localStorage.setItem(draftKey, JSON.stringify(savedDraft))
  const page = mount()
  fireEvent.change(page.getByRole('textbox', { name: 'sourceNumber' }), {
    target: { value: 'viking://resources/new' },
  })
  expect(JSON.parse(localStorage.getItem(draftKey)!).sources).toBe(
    'viking://resources/new',
  )
  expect(page.queryByRole('button', { name: 'restoreDraft' })).toBeNull()
  fireEvent.click(page.getByRole('button', { name: 'clear' }))
  expect(page.getAllByRole('textbox', { name: 'sourceNumber' })).toHaveLength(1)
  expect(
    (page.getByRole('textbox', { name: 'sourceNumber' }) as HTMLInputElement)
      .value,
  ).toBe('')
  expect(JSON.parse(localStorage.getItem(draftKey)!).sources).toBe('')
})

it('uses only the filesystem entry directory instead of merging old draft sources', () => {
  localStorage.setItem(draftKey, JSON.stringify(savedDraft))
  const page = mount({ sourceUri: 'viking://resources/current' })
  const fields = page.getAllByRole('textbox', { name: 'sourceNumber' })
  expect(fields).toHaveLength(1)
  expect((fields[0] as HTMLInputElement).value).toBe(
    'viking://resources/current',
  )
})

it('still fills all original sources when rerunning an existing task', async () => {
  api.fetchCompileTask.mockResolvedValueOnce({
    meta: {
      request: {
        skill: savedDraft.skill,
        from: savedDraft.sources.split('\n'),
        to: savedDraft.to,
      },
    },
  })
  const page = mount({ fromTask: 'original-task' })
  await waitFor(() =>
    expect(page.getAllByRole('textbox', { name: 'sourceNumber' })).toHaveLength(
      3,
    ),
  )
  expect(
    (page.getByRole('textbox', { name: 'target *' }) as HTMLInputElement).value,
  ).toBe(savedDraft.to)
})

it('preserves pending submission parameters ahead of new entry and rerun values', async () => {
  localStorage.setItem(draftKey, JSON.stringify(savedDraft))
  sessionStorage.setItem(storageKey, `${Date.now()}:pending`)
  prepareCompileHandoff(
    'recovery-test',
    'compile --from viking://resources/handoff --to viking://resources/new --skill viking://agent/skills/new',
  )
  api.fetchCompileTask.mockResolvedValueOnce({
    meta: {
      request: {
        skill: 'viking://agent/skills/other',
        from: ['viking://resources/rerun'],
        to: 'viking://resources/rerun-output',
      },
    },
  })
  const page = mount({
    fromTask: 'different-task',
    sourceUri: 'viking://resources/current',
  })
  await waitFor(() => expect(api.fetchCompileTask).toHaveBeenCalled())
  expect(
    page
      .getAllByRole('textbox', { name: 'sourceNumber' })
      .map((field) => (field as HTMLInputElement).value),
  ).toEqual(savedDraft.sources.split('\n'))
  expect(
    (page.getByRole('textbox', { name: 'target *' }) as HTMLInputElement).value,
  ).toBe(savedDraft.to)
  expect(page.queryByRole('button', { name: 'restoreDraft' })).toBeNull()
})

it('blocks a direct form submission when execution requirements are not met', async () => {
  prepareCompileHandoff(
    'recovery-test',
    'compile --from viking://resources/source --to viking://resources/wiki --skill viking://system/compile/example-skills/llm-wiki',
  )
  api.fetchCapabilities.mockResolvedValue({
    configured: false,
    can_create: false,
  })
  const page = mount()
  page.submit()
  await waitFor(() =>
    expect(
      page.getByRole('button', { name: 'start' }).hasAttribute('disabled'),
    ).toBe(true),
  )
  expect(api.createCompile).not.toHaveBeenCalled()
})

it('still recovers an accepted task when execution is no longer configured', async () => {
  const key = `${Date.now()}:accepted-task`
  sessionStorage.setItem(storageKey, key)
  api.fetchCapabilities.mockResolvedValue({
    configured: false,
    can_create: false,
  })
  api.lookupSubmission.mockResolvedValueOnce(task)
  const page = mount()
  page.submit()
  await waitFor(() => expect(api.navigate).toHaveBeenCalled())
  expect(api.createCompile).not.toHaveBeenCalled()
})
const configuredTarget = {
  ...savedDraft,
  from: savedDraft.sources.split('\n'),
  auto_update: true,
  update_schedule: { frequency: 'weekly', weekday: 3, hour: 14, minute: 30 },
  next_run_at: '2026-11-04T14:30:00+08:00',
}

it('saves a weekly plan without navigating to a task or leaving a submission recovery key', async () => {
  prepareCompileHandoff(
    'recovery-test',
    'compile --from viking://resources/source --to viking://resources/wiki --skill viking://agent/skills/wiki',
  )
  api.createCompile.mockResolvedValueOnce(configuredTarget)
  const page = mount()
  fireEvent.click(page.getByRole('checkbox', { name: 'schedule.enabled' }))
  await choose(
    page.getByRole('combobox', { name: 'schedule.frequency' }),
    'schedule.weekly',
  )
  await choose(
    page.getByRole('combobox', { name: 'schedule.weekday' }),
    'schedule.weekdays.3',
  )
  await choose(page.getByRole('combobox', { name: 'schedule.hour' }), '14')
  await choose(page.getByRole('combobox', { name: 'schedule.minute' }), '30')
  page.submit()
  await waitFor(() =>
    expect(api.navigate).toHaveBeenCalledWith({
      to: '/compile',
      search: { tab: 'schedules' },
    }),
  )
  expect(api.createCompile.mock.calls[0][0].args).toEqual({
    auto_update: true,
    update_schedule: configuredTarget.update_schedule,
  })
  expect(sessionStorage.getItem(storageKey)).toBeNull()
})

it('keeps a failed plan save retryable without task submission recovery', async () => {
  prepareCompileHandoff(
    'recovery-test',
    'compile --from viking://resources/source --to viking://resources/wiki --skill viking://agent/skills/wiki',
  )
  api.createCompile
    .mockRejectedValueOnce(new Error('response lost'))
    .mockResolvedValueOnce(configuredTarget)
  const page = mount()
  fireEvent.click(page.getByRole('checkbox', { name: 'schedule.enabled' }))
  page.submit()
  await waitFor(() => expect(page.getByRole('alert')).toBeTruthy())
  expect(sessionStorage.getItem(storageKey)).toBeNull()
  expect(page.queryByText('unknownSubmission')).toBeNull()
  page.submit()
  await waitFor(() => expect(api.navigate).toHaveBeenCalled())
  expect(api.lookupSubmission).not.toHaveBeenCalled()
})

it('loads a registered target and disables its schedule without requiring an execution runtime', async () => {
  api.fetchCompileConfig.mockResolvedValueOnce(configuredTarget)
  api.updateCompileConfig.mockResolvedValueOnce({
    ...configuredTarget,
    auto_update: false,
    update_schedule: null,
  })
  api.fetchCapabilities.mockResolvedValue({
    configured: false,
    can_create: false,
  })
  const page = mount({ target: savedDraft.to })
  await waitFor(() =>
    expect(
      (
        page.getByRole('checkbox', {
          name: 'schedule.enabled',
        }) as HTMLInputElement
      ).checked,
    ).toBe(true),
  )
  expect(
    (page.getByRole('textbox', { name: 'target *' }) as HTMLInputElement)
      .readOnly,
  ).toBe(true)
  fireEvent.click(page.getByRole('checkbox', { name: 'schedule.enabled' }))
  page.submit()
  await waitFor(() =>
    expect(api.updateCompileConfig).toHaveBeenCalledWith({
      to: savedDraft.to,
      from: configuredTarget.from,
      skill: savedDraft.skill,
      instruction: savedDraft.instruction,
      auto_update: false,
      update_schedule: null,
    }),
  )
  expect(api.createCompile).not.toHaveBeenCalled()
})

it('refetches a fresh cached configuration before enabling the editor', async () => {
  api.fetchCompileConfig.mockResolvedValueOnce({
    ...configuredTarget,
    auto_update: false,
    update_schedule: null,
  })
  const page = mount({ target: savedDraft.to }, configuredTarget)
  page.submit()
  expect(api.updateCompileConfig).not.toHaveBeenCalled()
  await waitFor(() =>
    expect(
      (page.getByRole('textbox', { name: 'target *' }) as HTMLInputElement)
        .value,
    ).toBe(savedDraft.to),
  )
  await waitFor(() =>
    expect(
      page
        .getByRole('button', { name: 'schedule.save' })
        .hasAttribute('disabled'),
    ).toBe(false),
  )
  expect(
    (
      page.getByRole('checkbox', {
        name: 'schedule.enabled',
      }) as HTMLInputElement
    ).checked,
  ).toBe(false)
})

it('loads fresh configuration after a failed read instead of submitting cached settings', async () => {
  const freshConfig = {
    ...configuredTarget,
    from: ['viking://resources/new-source'],
    skill: 'viking://agent/skills/new-skill',
    instruction: 'Use the latest configuration',
    auto_update: false,
    update_schedule: null,
  }
  api.fetchCompileConfig.mockReset()
  api.fetchCompileConfig
    .mockRejectedValueOnce(new Error('Network unavailable'))
    .mockResolvedValueOnce(freshConfig)
  api.updateCompileConfig.mockResolvedValueOnce(freshConfig)
  const page = mount(
    { target: savedDraft.to },
    configuredTarget,
    Date.now() - 20_000,
  )
  await page.findByRole('alert')
  page.submit()
  expect(api.updateCompileConfig).not.toHaveBeenCalled()
  fireEvent.click(page.getByRole('button', { name: 'Retry read' }))
  await waitFor(() =>
    expect(
      page
        .getByRole('button', { name: 'schedule.save' })
        .hasAttribute('disabled'),
    ).toBe(false),
  )
  page.submit()
  await waitFor(() =>
    expect(api.updateCompileConfig).toHaveBeenCalledWith({
      to: savedDraft.to,
      from: freshConfig.from,
      skill: freshConfig.skill,
      instruction: freshConfig.instruction,
      auto_update: false,
      update_schedule: null,
    }),
  )
})

afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
  vi.unstubAllGlobals()
})

it('submits over HTTP origins without crypto.randomUUID', async () => {
  const getRandomValues = globalThis.crypto.getRandomValues.bind(
    globalThis.crypto,
  )
  vi.stubGlobal('crypto', { getRandomValues })
  prepareCompileHandoff(
    'recovery-test',
    'compile --from viking://resources/source --to viking://resources/wiki --skill viking://agent/skills/wiki',
  )
  api.createCompile.mockResolvedValueOnce(task)

  const page = mount()
  page.submit()

  await waitFor(() => expect(api.createCompile).toHaveBeenCalledOnce())
  expect(api.createCompile.mock.calls[0][1]).toMatch(
    /^[0-9]+:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
  )
})

it('recovers an accepted submission after response loss and reload without resubmitting missing args', async () => {
  prepareCompileHandoff(
    'recovery-test',
    `compile --from viking://resources/source --to viking://resources/wiki --skill viking://agent/skills/wiki --args '{"mode":"summary"}'`,
  )
  api.createCompile.mockRejectedValueOnce(
    new OvClientError({ code: 'NETWORK_ERROR', message: 'Response lost' }),
  )
  let page = mount()
  page.submit()
  await waitFor(() => expect(page.getByText('retry')).toBeTruthy())
  const originalKey = sessionStorage.getItem(storageKey)
  expect(api.createCompile.mock.calls[0][0].args).toEqual({ mode: 'summary' })
  page.unmount()
  api.lookupSubmission.mockResolvedValueOnce(task)
  page = mount()
  page.submit()
  await waitFor(() =>
    expect(api.navigate).toHaveBeenCalledWith({
      to: '/compile/tasks/$taskId',
      params: { taskId: task.task_id },
    }),
  )
  expect(api.lookupSubmission).toHaveBeenCalledWith(originalKey)
  expect(api.createCompile).toHaveBeenCalledTimes(1)
  expect(sessionStorage.getItem(storageKey)).toBeNull()
})

it('keeps the restored key after a lookup race and conflict, then finds the original task', async () => {
  const key = `${Date.now()}:original-submission`
  sessionStorage.setItem(storageKey, key)
  localStorage.setItem(
    'compile-draft:recovery-test',
    JSON.stringify({
      skill: 'viking://agent/skills/wiki',
      sources: 'viking://resources/source',
      to: 'viking://resources/wiki',
      instruction: '',
    }),
  )
  api.lookupSubmission
    .mockRejectedValueOnce(
      new OvClientError({ code: 'NOT_FOUND', message: 'Not yet visible' }),
    )
    .mockResolvedValueOnce(task)
  api.createCompile.mockRejectedValueOnce(
    new OvClientError({ code: 'CONFLICT', message: 'Different args' }),
  )
  const page = mount()
  page.submit()
  await waitFor(() => expect(page.getByRole('alert')).toBeTruthy())
  expect(sessionStorage.getItem(storageKey)).toBe(key)
  expect(api.createCompile.mock.calls[0][1]).toBe(key)
  page.submit()
  await waitFor(() => expect(api.navigate).toHaveBeenCalled())
  expect(api.createCompile).toHaveBeenCalledTimes(1)
  expect(api.lookupSubmission.mock.calls).toEqual([[key], [key]])
})

it('does not resubmit or discard the recovery key when lookup is unavailable', async () => {
  const key = `${Date.now()}:original-submission`
  sessionStorage.setItem(storageKey, key)
  api.lookupSubmission.mockRejectedValueOnce(
    new OvClientError({ code: 'NETWORK_ERROR', message: 'Offline' }),
  )
  const page = mount()
  page.submit()
  await waitFor(() => expect(page.getByRole('alert')).toBeTruthy())
  expect(api.lookupSubmission).toHaveBeenCalledWith(key)
  expect(api.createCompile).not.toHaveBeenCalled()
  expect(sessionStorage.getItem(storageKey)).toBe(key)
})
