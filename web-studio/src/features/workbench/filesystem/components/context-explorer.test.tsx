// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { ContextTree } from './context-explorer'

const mocks = vi.hoisted(() => ({ list: vi.fn(), stat: vi.fn() }))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))
vi.mock('#/features/workbench/resources/lib/api', () => ({
  fetchFsStat: mocks.stat,
}))
vi.mock('#/shared/api/ov-client', () => ({
  getOvClient: () => ({ instance: { get: mocks.list } }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

function setup() {
  const children: Partial<Record<string, string[]>> = {
    'viking://': ['agent', 'resources', 'system', 'user'],
    'viking://resources/': ['demo'],
    'viking://user/': ['default'],
  }
  mocks.list.mockImplementation(async (_route, { params: { uri } }) => ({
    data: {
      result: (children[uri] ?? []).map((name) => ({
        uri: `${uri}${name}`,
        isDir: true,
      })),
    },
  }))
  const onOpenSearch = vi.fn()
  const onRefresh = vi.fn(async () => {})
  const onSelectDirectory = vi.fn()
  const onExpandedKeysChange = vi.fn()
  const client = new QueryClient()
  client.setQueryData(['viking-fs-ls', 'viking://'], [])
  render(
    <QueryClientProvider client={client}>
      <ContextTree
        onOpenSearch={onOpenSearch}
        onRefresh={onRefresh}
        currentUri="viking://"
        expandedKeys={new Set(['viking://resources/', 'viking://user/'])}
        onExpandedKeysChange={onExpandedKeysChange}
        onSelectDirectory={onSelectDirectory}
        onSelectFile={vi.fn()}
      />
    </QueryClientProvider>,
  )
  return {
    client,
    onSelectDirectory,
    onExpandedKeysChange,
    onOpenSearch,
    onRefresh,
  }
}

it('adapts the selected URI into Studio file metadata', async () => {
  const { onSelectDirectory } = setup()
  const directory = {
    uri: 'viking://resources',
    isDir: true,
    name: 'resources',
  }
  mocks.stat.mockResolvedValue(directory)
  await userEvent.click(await screen.findByText('resources', { exact: true }))
  await waitFor(() => expect(onSelectDirectory).toHaveBeenCalledWith(directory))
})

it('refreshes tree data after a Studio filesystem mutation', async () => {
  const { client } = setup()
  await screen.findByRole('treeitem', { name: /demo/ })
  const previousCalls = mocks.list.mock.calls.length
  await client.invalidateQueries({ queryKey: ['viking-fs-ls'] })
  await waitFor(() =>
    expect(mocks.list.mock.calls.length).toBeGreaterThan(previousCalls),
  )
})

it('opens Studio search and refreshes through the shared tree toolbar', async () => {
  const { onOpenSearch, onRefresh } = setup()
  await userEvent.click(screen.getByRole('button', { name: 'ide.search' }))
  expect(onOpenSearch).toHaveBeenCalledOnce()
  await userEvent.click(
    screen.getByRole('button', { name: 'explorer.refresh' }),
  )
  expect(onRefresh).toHaveBeenCalledOnce()
})

it('puts the complete user subtree first while preserving other branches', async () => {
  setup()
  await screen.findByRole('treeitem', { name: /demo/ })
  await screen.findByRole('treeitem', { name: /default/ })
  expect(
    screen
      .getAllByRole('treeitem')
      .map((item) => item.querySelector('.ov-file-tree__label')?.textContent),
  ).toEqual(['user', 'default', 'agent', 'resources', 'demo', 'system'])
})
