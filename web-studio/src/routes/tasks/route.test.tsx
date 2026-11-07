import studioI18n from '#/i18n/core'
import type * as ConnectionHealth from '#/features/connection/lib/health'
// @vitest-environment jsdom

import en from '#/i18n/locales/en/workspace'
import zh from '#/i18n/locales/zh-CN/workspace'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type * as TanStackRouter from '@tanstack/react-router'
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { createInstance } from 'i18next'
import type { ComponentType } from 'react'
import { I18nextProvider } from 'react-i18next'
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from 'vitest'

import { commitSession } from '#/features/workbench/sessions/lib/api'
import { TasksRoute } from '#/features/workbench/tasks/page'
import { ovClient } from '#/shared/api/ov-client/index'
import type { TaskRecord } from '#/features/workbench/tasks/lib/task-record'
import { postResources } from '#/gen/ov-client'
import { Route } from '#/routes/tasks/route'
import { toast } from 'sonner'

const clientMocks = vi.hoisted(() => ({ getTasks: vi.fn() }))

const credentialMocks = vi.hoisted(() => ({ users: vi.fn(), health: vi.fn() }))
vi.mock('#/shared/api/admin', () => ({
  fetchAdminUsers: credentialMocks.users,
}))
vi.mock('#/features/connection/lib/health', async (importOriginal) => ({
  ...(await importOriginal<typeof ConnectionHealth>()),
  fetchServerHealth: credentialMocks.health,
}))

const navigationMocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  search: {} as Record<string, string>,
}))
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof TanStackRouter>()),
  useNavigate: () => navigationMocks.navigate,
  useSearch: () => navigationMocks.search,
  Link: ({
    to,
    search,
    children,
  }: {
    to: string
    search: Record<string, string>
    children: React.ReactNode
  }) => (
    <a
      href={
        to +
        (Object.keys(search).length ? '?' + new URLSearchParams(search) : '')
      }
    >
      {children}
    </a>
  ),
}))

vi.mock('#/shared/api/ov-client/index', () => ({
  getOvResult: async (value: unknown) => value,
  getTasks: clientMocks.getTasks,
  ovClient: {
    getOptions: () => ({ baseUrl: 'http://localhost:1933' }),
    instance: { post: vi.fn() },
  },
}))

vi.mock('#/gen/ov-client', () => ({ postResources: vi.fn() }))
vi.mock('#/features/workbench/sessions/lib/api', () => ({
  commitSession: vi.fn(),
}))
vi.mock('sonner', () => ({
  toast: { info: vi.fn(), success: vi.fn(), error: vi.fn() },
}))
const connectionState = vi.hoisted(() => ({
  identityScopeKey: 'test/test',
  connection: {
    accountId: 'default',
    adminApiKey: 'management-key',
    apiKey: 'user-key',
    baseUrl: 'http://localhost:1933',
    userId: 'alice',
  },
  connectionRole: 'root' as 'root' | 'admin' | 'user' | 'unknown',
  serverMode: 'api_key' as 'api_key' | 'trusted',
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => connectionState,
}))
vi.mock('#/features/workbench/monitoring/components/queue-status-card', () => ({
  QueueStatusCard: () => null,
}))
const detailMocks = vi.hoisted(() => ({ render: vi.fn() }))
vi.mock('#/features/workbench/tasks/components/task-detail-sheet', () => ({
  TaskDetailSheet: (props: unknown) => {
    detailMocks.render(props)
    return null
  },
}))

const TaskPage = Route.options.component as ComponentType & {
  preload?: () => Promise<void>
}
beforeAll(async () => {
  await TaskPage.preload?.()
})

let records: TaskRecord[]
const queryClients: QueryClient[] = []

function runningTasks(count: number): TaskRecord[] {
  return Array.from({ length: count }, (_, index) => ({
    task_id: `task-${index + 1}`,
    task_type: index < 8 ? 'session_commit' : 'add_resource',
    resource_id: `resource-${index + 1}`,
    status: 'running',
    created_at: 100 + index,
  }))
}

async function renderPage(
  lng = 'en',
  management = false,
  existingClient?: QueryClient,
) {
  const i18n = createInstance()
  await i18n.init({
    lng,
    resources: { en, 'zh-CN': zh },
    interpolation: { escapeValue: false },
  })
  const queryClient =
    existingClient ??
    new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
  queryClients.push(queryClient)
  render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        {management ? <TasksRoute management /> : <TaskPage />}
      </I18nextProvider>
    </QueryClientProvider>,
  )
  return userEvent.setup()
}

function taskRows() {
  return screen.queryAllByRole('row', {
    name: /^(View details for task |查看任务 )/,
  })
}

function expectRunningRows(count: number) {
  expect(taskRows()).toHaveLength(count)
  for (const row of taskRows()) {
    expect(within(row).getByText('Running')).toBeDefined()
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  navigationMocks.search = {}
  connectionState.connection.accountId = 'default'
  connectionState.connection.adminApiKey = 'management-key'
  connectionState.connection.apiKey = 'user-key'
  connectionState.connectionRole = 'root'
  connectionState.serverMode = 'api_key'
  credentialMocks.users.mockImplementation(async (_connection, accountId) => [
    {
      accountId,
      userId: 'space-admin',
      role: 'admin',
      apiKey: `${accountId}-admin-key`,
    },
  ])
  credentialMocks.health.mockImplementation(async () => ({
    role: 'admin',
    account_id: connectionState.connection.accountId,
    user_id: 'space-admin',
  }))
  records = runningTasks(12)
  clientMocks.getTasks.mockReset()
  // Model the API contract: filtering precedes ordering and the result limit.
  clientMocks.getTasks.mockImplementation(({ query }) =>
    records
      .filter((task) => !query.status || task.status === query.status)
      .filter((task) => !query.task_type || task.task_type === query.task_type)
      .sort((left, right) => Number(right.created_at) - Number(left.created_at))
      .slice(0, query.limit),
  )
})

afterEach(() => {
  cleanup()
  for (const client of queryClients.splice(0)) client.clear()
})

describe('task status presentation', () => {
  it('opens the workbench task list with the user credential and links back to the selected file', async () => {
    connectionState.connectionRole = 'user'
    connectionState.connection.adminApiKey = ''
    navigationMocks.search = { file: 'viking://resources/notes.md' }

    await renderPage()

    await waitFor(() => expect(taskRows().length).toBeGreaterThan(0))
    expect(clientMocks.getTasks).toHaveBeenCalledWith({ query: { limit: 200 } })
    const back = screen.getByRole('link', { name: 'Back to workbench' })
    expect(back.getAttribute('href')).toBe(
      '/filesystem?file=viking%3A%2F%2Fresources%2Fnotes.md',
    )
  })

  it('keeps all twelve running tasks running in both rows and the count', async () => {
    await renderPage()
    await screen.findByRole('row', { name: 'View details for task task-12' })

    expectRunningRows(12)
    expect(screen.getByText('12 / 0')).toBeDefined()
  })

  it('shows no pending tasks when all tasks are running, including after type filtering', async () => {
    const user = await renderPage()
    await screen.findByRole('row', { name: 'View details for task task-12' })

    await user.click(screen.getByRole('combobox', { name: 'Task status' }))
    await user.click(await screen.findByRole('option', { name: 'Pending' }))
    await screen.findByText('No matching tasks')
    expect(taskRows()).toHaveLength(0)
    expect(screen.getByText('0 / 0')).toBeDefined()

    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    await screen.findByRole('row', { name: 'View details for task task-12' })
    await user.click(screen.getByRole('combobox', { name: 'Task type' }))
    await user.click(
      await screen.findByRole('option', { name: 'Resource processing' }),
    )
    await screen.findByText('4 / 0')
    expectRunningRows(4)
  })

  it.each([
    ['en', 'Pending', 'Task status'],
    ['zh-CN', '等待中', '任务状态'],
  ])(
    'renders and filters actual pending tasks in %s',
    async (lng, pendingLabel, filterLabel) => {
      records.push(
        ...[1, 2].map((index) => ({
          task_id: `pending-${index}`,
          task_type: 'add_resource',
          resource_id: `pending-resource-${index}`,
          status: 'pending',
          created_at: 200 + index,
        })),
      )
      const user = await renderPage(lng)
      await screen.findByRole('row', { name: /pending-2/ })
      expect(screen.getByText('12 / 2')).toBeDefined()

      await user.click(screen.getByRole('combobox', { name: filterLabel }))
      await user.click(
        await screen.findByRole('option', { name: pendingLabel }),
      )
      await screen.findByText('0 / 2')
      expect(taskRows()).toHaveLength(2)
      for (const row of taskRows()) {
        expect(within(row).getByText(pendingLabel)).toBeDefined()
      }
    },
  )

  it('preserves task statuses when grouping and pagination change the visible rows', async () => {
    records = runningTasks(26)
    records[25].resource_id = records[0].resource_id
    const user = await renderPage()
    await screen.findByRole('row', { name: 'View details for task task-26' })
    expectRunningRows(20)
    expect(screen.getByText('25 / 0')).toBeDefined()

    await user.click(screen.getByRole('button', { name: 'Individual Tasks' }))
    expect(screen.getByText('26 / 0')).toBeDefined()
    expectRunningRows(20)

    await user.click(screen.getByRole('button', { name: 'Go to next page' }))
    expectRunningRows(6)
    expect(screen.getByText('26 / 0')).toBeDefined()
  })
  it('keeps separate compile runs and opens their dedicated detail page', async () => {
    records = ['cmp_one', 'cmp_two'].map((task_id) => ({
      task_id,
      task_type: 'compile',
      resource_id: 'same-source',
      status: 'running',
      created_at: 200,
    }))
    const user = await renderPage()
    await user.click(
      await screen.findByRole('row', { name: 'View details for task cmp_one' }),
    )
    expect(
      screen.getByRole('row', { name: 'View details for task cmp_two' }),
    ).toBeDefined()
    expect(navigationMocks.navigate).toHaveBeenCalledWith({
      to: '/compile/tasks/$taskId',
      params: { taskId: 'cmp_one' },
    })
  })
})

describe('session commit re-trigger feedback', () => {
  beforeEach(() => {
    records = [
      {
        task_id: 'failed-commit',
        task_type: 'session_commit',
        resource_id: 'session-1',
        status: 'failed',
        created_at: 100,
      },
    ]
    vi.mocked(commitSession).mockReset()
  })

  it.each([
    [
      'en',
      'no_messages',
      'No new task created: this session has no pending messages',
    ],
    ['zh-CN', 'no_messages', '未创建新任务：该会话没有待提交消息'],
    [
      'en',
      'all_within_keep_window',
      'No new task created: this session commit was skipped',
    ],
    ['zh-CN', 'all_within_keep_window', '未创建新任务：本次会话提交已跳过'],
    ['en', undefined, 'No new task created: this session commit was skipped'],
  ])(
    'reports skipped commits without success in %s (%s)',
    async (lng, reason, message) => {
      vi.mocked(commitSession).mockResolvedValue({
        status: 'skipped',
        reason,
        task_id: null,
      } as Awaited<ReturnType<typeof commitSession>>)
      const user = await renderPage(lng)
      await user.click(
        await screen.findByTitle(
          lng === 'en' ? 'Re-trigger Task' : '重新发起任务',
        ),
      )
      await waitFor(() => expect(toast.info).toHaveBeenCalledWith(message))
      expect(commitSession).toHaveBeenCalledWith(
        'session-1',
        undefined,
        undefined,
      )
      expect(toast.info).toHaveBeenCalledTimes(1)
      expect(toast.success).not.toHaveBeenCalled()
      expect(toast.error).not.toHaveBeenCalled()
    },
  )

  it('reports success when a new task is accepted', async () => {
    vi.mocked(commitSession).mockResolvedValue({
      status: 'accepted',
      task_id: 'new-task',
    } as Awaited<ReturnType<typeof commitSession>>)
    const user = await renderPage()
    await user.click(await screen.findByTitle('Re-trigger Task'))
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1))
    expect(toast.info).not.toHaveBeenCalled()
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('reports request errors without success', async () => {
    vi.mocked(commitSession).mockRejectedValue(new Error('Commit failed'))
    const user = await renderPage()
    await user.click(await screen.findByTitle('Re-trigger Task'))
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(
        studioI18n.t('studio:errors.requestFailed'),
        { description: 'Commit failed' },
      ),
    )
    expect(toast.info).not.toHaveBeenCalled()
    expect(toast.success).not.toHaveBeenCalled()
  })
})

it('groups processing and total time without inventing legacy timing', async () => {
  records = [
    {
      task_id: 'measured',
      status: 'completed',
      task_type: 'add_resource',
      created_at: 100,
      updated_at: 700,
      processing_seconds: 61,
    },
    {
      task_id: 'pending',
      status: 'pending',
      task_type: 'add_resource',
      created_at: 100,
      processing_seconds: 0,
    },
    {
      task_id: 'legacy',
      status: 'completed',
      task_type: 'add_resource',
      created_at: 100,
      updated_at: 700,
    },
  ]
  await renderPage()
  const measured = await screen.findByRole('row', {
    name: 'View details for task measured',
  })
  expect(within(measured).getByText('1m 1s')).toBeDefined()
  expect(within(measured).queryByText(/Waiting Time/)).toBeNull()
  expect(screen.getByRole('columnheader', { name: 'Duration' })).toBeDefined()
  expect(within(measured).getByText('1m 1s').closest('td')).toBe(
    within(measured).getByText('10m').closest('td'),
  )
  expect(within(measured).getByText('10m')).toBeDefined()
  expect(
    within(
      screen.getByRole('row', { name: 'View details for task pending' }),
    ).getByText('Not started'),
  ).toBeDefined()
  const legacy = screen.getByRole('row', {
    name: 'View details for task legacy',
  })
  expect(within(legacy).getByText('Not recorded')).toBeDefined()
})

describe('management task scope', () => {
  it.each(['click', 'Enter', 'Space'])(
    'opens compile details with the management credential via %s',
    async (action) => {
      connectionState.connectionRole = 'admin'
      records = [
        {
          task_id: 'manager-compile',
          task_type: 'compile',
          resource_id: 'same-source',
          status: 'completed',
          created_at: 200,
        },
      ]
      const user = await renderPage('en', true)
      const row = await screen.findByRole('row', {
        name: 'View details for task manager-compile',
      })
      if (action === 'click') await user.click(row)
      else {
        row.focus()
        await user.keyboard(action === 'Enter' ? '{Enter}' : ' ')
      }
      expect(navigationMocks.navigate).not.toHaveBeenCalled()
      expect(detailMocks.render).toHaveBeenLastCalledWith(
        expect.objectContaining({
          open: true,
          taskId: 'manager-compile',
          identityScopeKey: expect.stringMatching(/^management\//),
          headers: { 'X-API-Key': 'management-key' },
        }),
      )
    },
  )

  it.each([
    ['default', ''],
    ['default', 'user-key'],
    ['other-space', ''],
    ['other-space', 'user-key'],
  ])(
    'queries space %s using its Admin key when Root and data key %s are present',
    async (account, apiKey) => {
      connectionState.connection.accountId = account
      connectionState.connection.apiKey = apiKey
      await renderPage('en', true)
      await screen.findByRole('row', { name: 'View details for task task-1' })
      expect(clientMocks.getTasks).toHaveBeenCalledWith({
        headers: { 'X-API-Key': `${account}-admin-key` },
        query: { limit: 200 },
      })
      expect(credentialMocks.users).toHaveBeenCalledWith(
        expect.objectContaining({ apiKey: 'management-key' }),
        account,
        true,
      )
    },
  )

  it('uses the Account Admin credential even when a data key is present', async () => {
    connectionState.connectionRole = 'admin'
    await renderPage('en', true)
    await screen.findByRole('row', { name: 'View details for task task-1' })
    expect(clientMocks.getTasks).toHaveBeenCalledWith({
      headers: { 'X-API-Key': 'management-key' },
      query: { limit: 200 },
    })
  })

  it('does not reuse management task data after changing only the management key', async () => {
    connectionState.connectionRole = 'admin'
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    })
    records = [
      { task_id: 'first-admin-task', status: 'completed', created_at: 100 },
    ]
    await renderPage('en', true, client)
    await screen.findByRole('row', {
      name: 'View details for task first-admin-task',
    })
    cleanup()

    connectionState.connection.adminApiKey = 'other-management-key'
    records = [
      { task_id: 'second-admin-task', status: 'completed', created_at: 200 },
    ]
    await renderPage('en', true, client)
    await screen.findByRole('row', {
      name: 'View details for task second-admin-task',
    })
    expect(
      screen.queryByRole('row', {
        name: 'View details for task first-admin-task',
      }),
    ).toBeNull()
    expect(clientMocks.getTasks).toHaveBeenLastCalledWith({
      headers: { 'X-API-Key': 'other-management-key' },
      query: { limit: 200 },
    })
  })

  it('uses the same trusted admin identity to list and retry resource tasks', async () => {
    connectionState.serverMode = 'trusted'
    connectionState.connection.apiKey = ''
    records = [
      {
        task_id: 'failed-import',
        task_type: 'add_resource',
        resource_id: 'viking://resources/article',
        status: 'failed',
        created_at: 100,
      },
    ]
    vi.mocked(ovClient.instance.post).mockResolvedValue({
      data: { status: 'ok' },
    })
    const user = await renderPage('en', true)
    await user.click(await screen.findByTitle('Re-trigger Task'))
    const headers = {
      'X-API-Key': 'management-key',
      'X-OpenViking-Role': 'admin',
    }
    expect(clientMocks.getTasks).toHaveBeenCalledWith({
      headers,
      query: { limit: 200 },
    })
    expect(ovClient.instance.post).toHaveBeenCalledWith(
      '/api/v1/content/reindex',
      { uri: 'viking://resources/article', wait: false },
      { headers },
    )
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1))
  })

  it('keeps the trusted admin identity when retrying a session commit', async () => {
    connectionState.serverMode = 'trusted'
    records = [
      {
        task_id: 'failed-commit',
        task_type: 'session_commit',
        resource_id: 'session-1',
        status: 'failed',
        created_at: 100,
      },
    ]
    vi.mocked(commitSession).mockResolvedValue({
      status: 'accepted',
      task_id: 'new-task',
    } as Awaited<ReturnType<typeof commitSession>>)
    const user = await renderPage('en', true)
    await user.click(await screen.findByTitle('Re-trigger Task'))
    expect(commitSession).toHaveBeenCalledWith('session-1', undefined, {
      'X-API-Key': 'management-key',
      'X-OpenViking-Role': 'admin',
    })
    await waitFor(() => expect(toast.success).toHaveBeenCalledTimes(1))
  })
})

it.each([false, true])(
  'retries a URL import in management mode %s using its credential',
  async (management) => {
    connectionState.connectionRole = 'admin'
    records = [
      {
        task_id: 'failed-import',
        task_type: 'add_resource',
        resource_id: 'https://example.com/article',
        status: 'failed',
        created_at: 100,
      },
    ]
    vi.mocked(postResources).mockResolvedValue(
      {} as Awaited<ReturnType<typeof postResources>>,
    )
    const user = await renderPage('en', management)
    await user.click(await screen.findByTitle('Re-trigger Task'))
    await waitFor(() =>
      expect(postResources).toHaveBeenCalledWith({
        ...(management ? { headers: { 'X-API-Key': 'management-key' } } : {}),
        body: {
          path: 'https://example.com/article',
          reason: 'Re-queued task: failed-import',
        },
      }),
    )
  },
)
