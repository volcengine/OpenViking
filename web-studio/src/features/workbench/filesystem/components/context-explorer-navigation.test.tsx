// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { ContextTree } from './context-explorer'

vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ connection: {}, identityScopeKey: 'test' }),
}))
vi.mock('#/features/workbench/resources/lib/api', () => ({
  fetchFsStat: async (uri: string) => ({ uri, isDir: true }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(cleanup)

const get = vi.hoisted(() => vi.fn())
vi.mock('#/shared/api/ov-client', () => ({
  getOvClient: () => ({ instance: { get } }),
}))

it('keeps existing tree nodes and cached branches when Studio selects a directory', async () => {
  const names: Partial<Record<string, string[]>> = {
    'viking://': ['resources', 'user'],
    'viking://resources/': ['alpha', 'beta'],
    'viking://resources/alpha/': ['note.md'],
    'viking://resources/beta/': ['other.md'],
  }
  get.mockImplementation(async (_route, { params: { uri } }) => ({
    data: {
      status: 'ok',
      result: (names[uri] ?? []).map((name) => ({
        uri: `${uri}${name}`,
        isDir: !name.endsWith('.md'),
      })),
    },
  }))
  function StudioNavigation() {
    const [uri, setUri] = useState('viking://resources')
    const [expanded, setExpanded] = useState(new Set(['viking://resources/']))
    return (
      <ContextTree
        currentUri={uri}
        expandedKeys={expanded}
        onExpandedKeysChange={setExpanded}
        onSelectDirectory={(entry) => setUri(entry.uri)}
        onSelectFile={vi.fn()}
      />
    )
  }
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <StudioNavigation />
    </QueryClientProvider>,
  )
  const alpha = await screen.findByRole('treeitem', { name: /alpha/ })
  const sibling = screen.getByRole('treeitem', { name: /user/ })
  const removed: Node[] = []
  const observer = new MutationObserver((records) => {
    for (const record of records)
      for (const node of record.removedNodes) {
        if (node === sibling || node.contains(sibling)) removed.push(node)
      }
  })
  observer.observe(screen.getByRole('tree'), { childList: true, subtree: true })
  try {
    await userEvent.click(alpha)
    await screen.findByRole('treeitem', { name: /note.md/ })
    await waitFor(() =>
      expect(alpha.getAttribute('aria-selected')).toBe('true'),
    )
    await userEvent.click(screen.getByRole('treeitem', { name: /beta/ }))
    await screen.findByRole('treeitem', { name: /other.md/ })
    await waitFor(() =>
      expect(
        screen
          .getByRole('treeitem', { name: /beta/ })
          .getAttribute('aria-selected'),
      ).toBe('true'),
    )
    expect(removed).toHaveLength(0)
    expect(sibling.isConnected).toBe(true)
    expect(screen.getByRole('treeitem', { name: /note.md/ })).toBeTruthy()
    expect(
      get.mock.calls.filter(
        ([, options]) => options.params.uri === 'viking://',
      ),
    ).toHaveLength(1)
    await userEvent.click(
      screen.getByRole('button', { name: 'Collapse resources' }),
    )
    expect(screen.queryByRole('treeitem', { name: /note.md/ })).toBeNull()
    get.mockClear()
    await client.invalidateQueries({ queryKey: ['viking-fs-ls'] })
    expect(get.mock.calls.map(([, options]) => options.params.uri)).toEqual([
      'viking://',
    ])
    await userEvent.click(
      screen.getByRole('button', { name: 'Expand resources' }),
    )
    expect(
      await screen.findByRole('treeitem', { name: /note.md/ }),
    ).toBeTruthy()
  } finally {
    observer.disconnect()
  }
})
