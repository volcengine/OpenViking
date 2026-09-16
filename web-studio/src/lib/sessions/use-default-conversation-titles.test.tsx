// @vitest-environment jsdom
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { useDefaultConversationTitles } from './use-default-conversation-titles'

const mocks = vi.hoisted(() => ({ fetch: vi.fn(), save: vi.fn() }))
vi.mock('./api', () => ({ fetchSessionFirstTitle: mocks.fetch }))
vi.mock('./use-session-titles', () => ({
  useSessionTitles: () => ({
    getTitle: (id: string) => (id === 'custom' ? 'My title' : id),
    setTitle: mocks.save,
  }),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
it('backfills the first user text without fetching or replacing a custom title', async () => {
  mocks.fetch.mockResolvedValue('First question')
  const client = new QueryClient()
  renderHook(
    () => useDefaultConversationTitles('account', ['missing', 'custom']),
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      ),
    },
  )
  await waitFor(() =>
    expect(mocks.save).toHaveBeenCalledWith('missing', 'First question'),
  )
  expect(mocks.fetch).toHaveBeenCalledExactlyOnceWith('missing')
})
