// @vitest-environment jsdom

import type * as CompileApi from '#/features/workbench/compile/lib/api'
import { CompileTaskResult } from '#/features/workbench/filesystem/components/terminal-panel'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ fetchCompileTask: vi.fn() }))
vi.mock('#/features/workbench/compile/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof CompileApi>()),
  fetchCompileTask: api.fetchCompileTask,
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'connection-a' }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { index?: number }) =>
      options?.index ? `${key} ${options.index}` : key,
  }),
}))

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

it('refreshes explorer data and opens the output URI after compilation completes', async () => {
  api.fetchCompileTask.mockResolvedValue({
    task_id: 'cmp-1',
    status: 'completed',
    resource_id: 'viking://resources/input-a, viking://resources/input-b',
    meta: {
      request: {
        from: ['viking://resources/input-a', 'viking://resources/input-b'],
        to: 'viking://resources/output',
        skill: 'viking://agent/skills/demo',
      },
    },
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const explorerKeys = [
    ['viking-fs-ls', 'viking://resources/', {}],
    ['viking-fs-tree', 'viking://resources/', {}],
    ['viking-fs-stat', 'viking://resources/output'],
    ['viking-file-read', 'viking://resources/output', '', {}],
  ]
  for (const key of explorerKeys) client.setQueryData(key, { stale: true })
  const onOpenResource = vi.fn()
  render(
    <QueryClientProvider client={client}>
      <CompileTaskResult taskId="cmp-1" onOpenResource={onOpenResource} />
    </QueryClientProvider>,
  )

  await waitFor(() => {
    expect(
      screen.getByRole('button', { name: 'viking://resources/output' }),
    ).not.toBeNull()
    for (const key of explorerKeys)
      expect(client.getQueryState(key)?.isInvalidated).toBe(true)
  })
  fireEvent.click(
    screen.getByRole('button', { name: 'viking://resources/output' }),
  )
  expect(onOpenResource).toHaveBeenCalledWith('viking://resources/output')
  expect(
    screen
      .getByText('viking://resources/input-a, viking://resources/input-b')
      .closest('button'),
  ).toBeNull()
  fireEvent.click(
    screen.getByRole('button', { name: 'viking://resources/input-b' }),
  )
  expect(onOpenResource).toHaveBeenCalledWith('viking://resources/input-b')
  client.clear()
})
