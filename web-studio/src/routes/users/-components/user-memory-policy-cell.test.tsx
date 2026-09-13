// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { UserMemoryPolicyCell } from './user-memory-policy-cell'
import type { UserMemoryPolicy } from '#/lib/user-memory-policy'

const api = vi.hoisted(() => ({
  fetch: vi.fn(),
  update: vi.fn(),
  error: vi.fn(),
}))
vi.mock('#/lib/admin', () => ({
  fetchUserMemorySettings: api.fetch,
  updateUserMemorySettings: api.update,
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: api.error } }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('#/components/ui/tooltip', () => ({
  Tooltip: ({ children }: { children: ReactNode }) => children,
  TooltipTrigger: ({ render: trigger }: { render: ReactNode }) => trigger,
  TooltipContent: () => null,
}))
vi.mock('#/components/ui/dialog', () => {
  const Container = ({ children }: { children: ReactNode }) => children
  return {
    Dialog: ({ open, children }: { open: boolean; children: ReactNode }) =>
      open ? children : null,
    DialogContent: Container,
    DialogHeader: Container,
    DialogTitle: Container,
    DialogDescription: Container,
  }
})
vi.mock('./memory-policy-picker', () => ({
  MemoryPolicyPicker: ({
    value,
    onSelect,
  }: {
    value: UserMemoryPolicy
    onSelect: (value: UserMemoryPolicy) => void
  }) => (
    <button onClick={() => onSelect(value)}>{JSON.stringify(value)}</button>
  ),
}))
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})
const oldPolicy = { memory_types: ['profile'] }
const newPolicy = { memory_types: ['preferences'] }
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <UserMemoryPolicyCell
        connection={{
          baseUrl: 'http://localhost',
          apiKey: 'key',
          accountId: 'a',
          userId: 'admin',
        }}
        user={{ accountId: 'a', userId: 'u', role: 'user' }}
      />
    </QueryClientProvider>,
  )
  return client
}
it('reads the latest policy before editing and preserves save error details', async () => {
  api.fetch
    .mockResolvedValueOnce({ memory_policy: oldPolicy })
    .mockResolvedValue({ memory_policy: newPolicy })
  api.update.mockRejectedValue(new Error('server validation error'))
  mount()
  fireEvent.click(
    await screen.findByRole('button', { name: 'memoryPolicy.editUser' }),
  )
  fireEvent.click(
    await screen.findByRole('button', { name: JSON.stringify(newPolicy) }),
  )
  await waitFor(() =>
    expect(api.update).toHaveBeenCalledWith(
      expect.anything(),
      'a',
      'u',
      newPolicy,
    ),
  )
  await waitFor(() =>
    expect(api.error).toHaveBeenCalledWith('memoryPolicy.saveFailed', {
      description: 'server validation error',
    }),
  )
})
it('does not open stale data when refresh fails', async () => {
  api.fetch
    .mockResolvedValueOnce({ memory_policy: oldPolicy })
    .mockRejectedValue(new Error('offline'))
  mount()
  fireEvent.click(
    await screen.findByRole('button', { name: 'memoryPolicy.editUser' }),
  )
  await screen.findByRole('button', { name: 'memoryPolicy.retry' })
  expect(
    screen.queryByRole('button', { name: JSON.stringify(oldPolicy) }),
  ).toBeNull()
})
it('refreshes mounted policies when the account prefix is invalidated', async () => {
  api.fetch
    .mockResolvedValueOnce({ memory_policy: oldPolicy })
    .mockResolvedValue({ memory_policy: newPolicy })
  const client = mount()
  await screen.findByRole('button', { name: 'memoryPolicy.editUser' })
  await client.invalidateQueries({
    queryKey: ['user-memory-settings', 'http://localhost', 'key', 'a'],
  })
  expect(
    client.getQueryData([
      'user-memory-settings',
      'http://localhost',
      'key',
      'a',
      'u',
    ]),
  ).toEqual({ memory_policy: newPolicy })
})
