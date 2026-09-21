// @vitest-environment jsdom
import type { ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { CompileForm } from './compile-form'
import { prepareCompileHandoff } from '../-lib/handoff'
import { OvClientError } from '#/lib/ov-client'

const api = vi.hoisted(() => ({
  createCompile: vi.fn(),
  lookupSubmission: vi.fn(),
  fetchCompileSkills: vi.fn(async () => []),
  fetchCompileTask: vi.fn(),
  navigate: vi.fn(),
}))
vi.mock('../-lib/api', () => api)
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => api.navigate,
  useBlocker: () => {},
  Link: ({ children }: { children: ReactNode }) => <span>{children}</span>,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    identityScopeKey: 'recovery-test',
    connection: { userId: 'alice' },
  }),
}))
vi.mock('./shared', () => ({
  CompileShell: ({ children }: { children: ReactNode }) => (
    <div>{children}</div>
  ),
  CompileError: () => <div role="alert">Submission error</div>,
}))
vi.mock('./skill-picker', () => ({ SkillPicker: () => null }))
vi.mock('#/routes/resources/-components/directory-picker-dialog', () => ({
  DirectoryPickerDialog: () => null,
}))
vi.mock('#/routes/resources/-lib/api', () => ({ fetchFileContent: vi.fn() }))

const storageKey = 'compile-submission:recovery-test'
const clients: QueryClient[] = []
const task = { task_id: 'cmp-original', status: 'pending' }
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  const page = render(
    <QueryClientProvider client={client}>
      <CompileForm />
    </QueryClientProvider>,
  )
  return {
    ...page,
    submit: () => fireEvent.submit(page.container.querySelector('form')!),
  }
}
beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
})
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
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
