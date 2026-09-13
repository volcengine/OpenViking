// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import en from '#/i18n/locales/en/workspace'
import zh from '#/i18n/locales/zh-CN/workspace'
import { TaskDetailSheet } from './task-detail-sheet'
import type { TaskRecord } from '../-lib/task-record'

const api = vi.hoisted(() => ({ getTask: vi.fn(), copy: vi.fn() }))
vi.mock('#/lib/ov-client', () => ({
  getTaskByTaskId: api.getTask,
  getOvResult: async (result: Promise<unknown>) => await result,
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const task: TaskRecord = {
  task_id: 'task-1',
  task_type: 'session_commit',
  resource_id: 'session-1',
  status: 'completed',
  created_at_iso: '2026-09-09T01:00:00Z',
  updated_at_iso: '2026-09-09T01:01:00Z',
  result: { memories: 2 },
  execution_events: {
    started_mid_task: true,
    dropped_count: 2,
    items: [
      {
        seq: 3,
        recorded_at: '2026-09-09T01:00:59.123Z',
        kind: 'status_changed',
        status: 'completed',
        stage: 'memory_extraction',
        operation: null,
        error: null,
      },
    ],
  },
}
const clients: QueryClient[] = []

async function showDetail(
  language = 'en',
  identity = 'acme/alice',
  existingClient?: QueryClient,
) {
  const i18n = createInstance()
  await i18n.init({
    lng: language,
    fallbackLng: 'en',
    resources: {
      en: { tasksPage: en.tasksPage },
      zh: { tasksPage: zh.tasksPage },
    },
  })
  const client =
    existingClient ??
    new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
  clients.push(client)
  const view = render(
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>
        <TaskDetailSheet
          open
          taskId="task-1"
          identityScopeKey={identity}
          onOpenChange={vi.fn()}
        />
      </QueryClientProvider>
    </I18nextProvider>,
  )
  return { client, ...view }
}

beforeEach(() => {
  api.getTask.mockReset().mockResolvedValue(structuredClone(task))
  api.copy.mockReset().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText: api.copy },
  })
})
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
  localStorage.clear()
})

describe('recorded task execution events', () => {
  it.each(['en', 'zh'])(
    'shows and copies backend events in %s without synthetic lines',
    async (language) => {
      localStorage.setItem(
        'ov_studio_task_history',
        JSON.stringify([{ ...task, resource_id: 'stale-private-session' }]),
      )
      await showDetail(language)
      const title = language === 'en' ? 'Task execution log' : '任务执行日志'
      const events = await screen.findByRole('region', { name: title })
      expect(api.getTask).toHaveBeenCalledWith({
        path: { task_id: 'task-1' },
        query: { include_events: true },
      })
      expect(within(events).getAllByRole('listitem')).toHaveLength(1)
      expect(events.querySelector('time')?.dateTime).toBe(
        '2026-09-09T01:00:59.123Z',
      )
      expect(screen.queryByText('stale-private-session')).toBeNull()
      expect(events.textContent).not.toContain('WorkerThread')
      fireEvent.click(
        within(events).getByRole('button', {
          name: language === 'en' ? 'Copy events' : '复制事件',
        }),
      )
      await waitFor(() => expect(api.copy).toHaveBeenCalledOnce())
      const copied = api.copy.mock.calls[0][0]
      expect(copied).toContain('task-1')
      expect(copied).toContain('session-1')
      expect(copied).toContain('2026-09-09T01:00:59.123Z')
      expect(copied).not.toContain('2026-09-09T01:00:00Z')
      expect(copied).toContain(
        language === 'en' ? '2 earlier events' : '2 条事件',
      )
      expect(copied).toContain(language === 'en' ? 'tracking began' : '仅包含')
    },
  )

  it.each([
    [undefined, 'The server did not provide task events.'],
    [null, 'No execution events were recorded for this task.'],
  ])(
    'explains unavailable history while preserving task details',
    async (history, message) => {
      api.getTask.mockResolvedValue({ ...task, execution_events: history })
      await showDetail()
      expect(await screen.findByText(message)).toBeDefined()
      expect(screen.getByText('session-1')).toBeDefined()
      expect(
        screen
          .getByRole('button', { name: 'Copy events' })
          .hasAttribute('disabled'),
      ).toBe(true)
    },
  )

  it('retains the last recorded events and reports a refresh error', async () => {
    const { client } = await showDetail()
    await screen.findByRole('region', { name: 'Task execution log' })
    api.getTask.mockRejectedValue(new Error('UNAUTHENTICATED: expired key'))
    await act(async () => {
      await client.refetchQueries({ queryKey: ['task-detail'] })
    })
    expect(await screen.findByRole('alert')).toBeDefined()
    expect(screen.getByRole('alert').textContent).toContain('UNAUTHENTICATED')
    expect(
      screen.getByRole('region', { name: 'Task execution log' }),
    ).toBeDefined()
  })

  it('keeps unfamiliar event types visible and never imports another identity cache', async () => {
    const changed = structuredClone(task)
    changed.execution_events!.items[0].kind = 'future_process_event'
    api.getTask.mockResolvedValue(changed)
    const first = await showDetail()
    expect(await screen.findByText('future_process_event')).toBeDefined()
    first.unmount()
    api.getTask.mockRejectedValue(new Error('NOT_FOUND: task not visible'))
    await showDetail('en', 'acme/bob', first.client)
    expect(await screen.findByText('NOT_FOUND: task not visible')).toBeDefined()
    expect(screen.queryByText('future_process_event')).toBeNull()
  })
})
