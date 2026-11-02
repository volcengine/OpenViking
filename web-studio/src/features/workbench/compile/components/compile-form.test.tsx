// @vitest-environment jsdom
import { CompileForm } from '#/features/workbench/compile/components/compile-form'
import { prepareCompileHandoff } from '#/features/workbench/compile/lib/handoff'
import { OvClientError } from '#/shared/api/ov-client/index'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  BUILTIN_SKILLS_ROOT: 'viking://system/compile/example-skills',
  createCompile: vi.fn(),
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
  CompileError: () => <div role="alert">Submission error</div>,
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

const storageKey = 'compile-submission:recovery-test'
const clients: QueryClient[] = []
const task = { task_id: 'cmp-original', status: 'pending' }
function mount(props: { fromTask?: string; sourceUri?: string } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
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
