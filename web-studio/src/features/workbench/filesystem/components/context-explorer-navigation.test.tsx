// @vitest-environment jsdom
import { OpenVikingProvider } from '@openviking/react/client'
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

it('keeps existing tree nodes and cached branches when Studio selects a directory', async () => {
  const requests: string[] = []
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(input instanceof Request ? input.url : String(input))
    const uri = url.searchParams.get('uri')!
    requests.push(uri)
    const names: Record<string, string[]> = {
      'viking://': ['resources', 'user'],
      'viking://resources': ['alpha', 'beta'],
      'viking://resources/alpha': ['note.md'],
      'viking://resources/beta': ['other.md'],
    }
    return new Response(
      JSON.stringify({
        status: 'ok',
        result: (names[uri] ?? []).map((name) => ({
          uri: `${uri}${uri.endsWith('/') ? '' : '/'}${name}`,
          isDir: !name.endsWith('.md'),
        })),
      }),
      { headers: { 'Content-Type': 'application/json' } },
    )
  })
  function StudioNavigation() {
    const [uri, setUri] = useState('viking://resources')
    const [expanded, setExpanded] = useState(new Set<string>())
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
  render(
    <QueryClientProvider client={new QueryClient()}>
      <OpenVikingProvider baseURL="http://tree.test" fetch={fetcher}>
        <StudioNavigation />
      </OpenVikingProvider>
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
    expect(requests.filter((uri) => uri === 'viking://')).toHaveLength(1)
  } finally {
    observer.disconnect()
  }
})
