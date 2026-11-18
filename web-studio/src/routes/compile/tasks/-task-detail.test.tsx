// @vitest-environment jsdom

import type * as CompileApi from '#/features/workbench/compile/lib/api'
import { Route } from '#/routes/compile/tasks/$taskId'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type * as TanStackRouter from '@tanstack/react-router'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import type { ComponentType, ReactNode } from 'react'
import { afterEach, beforeAll, beforeEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ fetchCompileTask: vi.fn() }))
vi.mock('#/features/workbench/compile/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof CompileApi>()),
  fetchCompileTask: api.fetchCompileTask,
}))
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof TanStackRouter>()),
  getRouteApi: () => ({ useParams: () => ({ taskId: 'cmp-1' }) }),
  useLocation: () => ({ state: {} }),
  useRouter: () => ({ history: { back: vi.fn() } }),
  Link: ({
    to,
    search,
    children,
  }: {
    to: string
    search?: Record<string, string>
    children: ReactNode
  }) => <a href={`${to}?${new URLSearchParams(search)}`}>{children}</a>,
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    identityScopeKey: 'connection-a',
    connectionRole: 'user',
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { language: 'en' },
  }),
}))
vi.mock('#/features/workbench/compile/components/context-link', () => ({
  ContextLink: ({ uri }: { uri: string }) => <span>{uri}</span>,
  useCompileSkillName: () => (uri: string) => uri,
}))
vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const DetailPage = Route.options.component as ComponentType & {
  preload?: () => Promise<void>
}
const taskKey = ['compile-task', 'connection-a', 'cmp-1']
const outputUri = 'viking://resources/output'
const outputKeys = [
  ['viking-fs-ls', `${outputUri}/`, {}],
  ['viking-fs-tree', 'viking://resources/', {}],
  ['viking-fs-stat', outputUri],
  ['viking-file-read', `${outputUri}/index.md`, '', {}],
  ['dir-picker', 'connection-a', outputUri],
]
const unrelatedKeys = [
  ['dir-picker', 'connection-b', outputUri],
  ['compile-list', 'connection-a'],
]
const clients: QueryClient[] = []

function task(status: 'running' | 'completed'): CompileApi.CompileTask {
  return {
    task_id: 'cmp-1',
    task_type: 'compile',
    status,
    meta: {
      request: {
        from: ['viking://resources/input'],
        to: outputUri,
        skill: 'viking://system/compile/example-skills/llm-wiki',
      },
    },
  }
}

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  for (const key of [...outputKeys, ...unrelatedKeys])
    client.setQueryData(key, { cached: true })
  const page = render(
    <QueryClientProvider client={client}>
      <DetailPage />
    </QueryClientProvider>,
  )
  return { client, page }
}

beforeAll(async () => {
  await DetailPage.preload?.()
})
beforeEach(() => {
  vi.spyOn(Route, 'useParams').mockReturnValue({ taskId: 'cmp-1' })
})
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
  vi.resetAllMocks()
  vi.restoreAllMocks()
})

it('invalidates cached output listings, trees, metadata, previews, and directory pickers when opening a completed task', async () => {
  api.fetchCompileTask.mockResolvedValue(task('completed'))
  const { client } = mount()

  const outputLink = await screen.findByRole('link', {
    name: 'viewGenerated',
  })
  expect(outputLink.getAttribute('href')).toBe(
    `/filesystem?${new URLSearchParams({ uri: outputUri })}`,
  )
  await waitFor(() => {
    for (const key of outputKeys)
      expect(client.getQueryState(key)?.isInvalidated).toBe(true)
  })
  for (const key of unrelatedKeys)
    expect(client.getQueryState(key)?.isInvalidated).toBe(false)
})

it('refreshes output caches only once when a running task completes', async () => {
  api.fetchCompileTask.mockResolvedValue(task('running'))
  const { client, page } = mount()

  await screen.findByText('statuses.running')
  for (const key of outputKeys)
    expect(client.getQueryState(key)?.isInvalidated).toBe(false)

  api.fetchCompileTask.mockResolvedValue(task('completed'))
  await act(async () => {
    await client.refetchQueries({ queryKey: taskKey })
  })
  await waitFor(() => {
    for (const key of outputKeys)
      expect(client.getQueryState(key)?.isInvalidated).toBe(true)
  })

  // Model the explorer having fetched fresh output after that invalidation.
  for (const key of outputKeys) client.setQueryData(key, { cached: false })
  page.rerender(
    <QueryClientProvider client={client}>
      <DetailPage />
    </QueryClientProvider>,
  )
  await act(async () => {
    await client.refetchQueries({ queryKey: taskKey })
  })
  for (const key of outputKeys)
    expect(client.getQueryState(key)?.isInvalidated).toBe(false)
})
