// @vitest-environment jsdom
import type { FileTreeRow } from '@openviking/react/components/file-tree'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { ContextTree } from './context-explorer'

const m = vi.hoisted(() => ({
  select: vi.fn(),
  toggleExpand: vi.fn(),
  refresh: vi.fn(),
  stat: vi.fn(),
  hook: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: { apiKey: '', baseUrl: '' },
    identityScopeKey: 'test',
  }),
}))
vi.mock('#/features/workbench/resources/lib/api', () => ({
  fetchFsStat: m.stat,
}))
vi.mock('@openviking/react/modules/file-browser', () => ({
  useFileBrowser: m.hook,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
const row = {
  kind: 'entry' as const,
  id: 'viking://resources',
  uri: 'viking://resources',
  name: 'resources',
  type: 'directory' as const,
  depth: 0,
  expanded: false,
}
function setup(rows: readonly FileTreeRow[] = [row]) {
  const onOpenSearch = vi.fn(),
    onRefresh = vi.fn(async () => {})
  m.hook.mockReturnValue({
    rows,
    selectedId: row.id,
    isReady: true,
    treeError: null,
    actions: {
      select: m.select,
      toggleExpand: m.toggleExpand,
      refresh: m.refresh,
    },
  })
  const onSelectDirectory = vi.fn(),
    onSelectFile = vi.fn(),
    onExpandedKeysChange = vi.fn()
  const client = new QueryClient()
  client.setQueryData(['viking-fs-ls', 'viking://'], [])
  render(
    <QueryClientProvider client={client}>
      <ContextTree
        onOpenSearch={onOpenSearch}
        onRefresh={onRefresh}
        currentUri="viking://"
        expandedKeys={new Set()}
        onExpandedKeysChange={onExpandedKeysChange}
        onSelectDirectory={onSelectDirectory}
        onSelectFile={onSelectFile}
      />
    </QueryClientProvider>,
  )
  return {
    client,
    onSelectDirectory,
    onSelectFile,
    onExpandedKeysChange,
    onOpenSearch,
    onRefresh,
  }
}
it('uses module rows and delegates selection to the module', async () => {
  setup()
  await userEvent.click(screen.getByRole('treeitem', { name: /resources/ }))
  expect(m.select).toHaveBeenCalledWith(
    expect.objectContaining({ uri: row.uri }),
  )
  expect(m.hook).toHaveBeenCalledWith(
    expect.objectContaining({ controlled: true, loadPreview: false }),
  )
})
it('adapts the selected URI into Studio file metadata', async () => {
  const { onSelectFile } = setup()
  const file = {
    uri: 'viking://resources/guide.md',
    isDir: false,
    name: 'guide.md',
  }
  m.stat.mockResolvedValue(file)
  m.hook.mock.calls.at(-1)![0].onUriChange(file.uri)
  await waitFor(() => expect(onSelectFile).toHaveBeenCalledWith(file))
})
it('refreshes module data after a Studio filesystem mutation', async () => {
  const { client } = setup()
  await client.invalidateQueries({ queryKey: ['viking-fs-ls'] })
  await waitFor(() => expect(m.refresh).toHaveBeenCalledTimes(1))
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

it('puts the complete user subtree first while preserving other branches', () => {
  const entry = (name: string, depth = 0) => ({
    ...row,
    id: `viking://${name}`,
    uri: `viking://${name}`,
    name: name.split('/').at(-1)!,
    depth,
    expanded: true,
  })
  const rows = [
    entry('agent'),
    entry('resources'),
    entry('resources/demo', 1),
    entry('system'),
    entry('user'),
    entry('user/default', 1),
  ]
  setup(rows)
  expect(
    screen
      .getAllByRole('treeitem')
      .map((item) => item.querySelector('.ov-file-tree__label')?.textContent),
  ).toEqual(['user', 'default', 'agent', 'resources', 'demo', 'system'])
  expect(rows.map((item) => item.name)).toEqual([
    'agent',
    'resources',
    'demo',
    'system',
    'user',
    'default',
  ])
})
