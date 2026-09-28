// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { UserGroups } from './user-groups'

const api = vi.hoisted(() => ({
  groups: vi.fn(),
  members: vi.fn(),
  users: vi.fn(),
  create: vi.fn(),
  remove: vi.fn(),
  change: vi.fn(),
  error: vi.fn(),
}))
vi.mock('#/lib/admin', () => ({
  fetchAdminGroups: api.groups,
  fetchAdminGroupMembers: api.members,
  fetchAdminUsersPage: api.users,
  createAdminGroup: api.create,
  deleteAdminGroup: api.remove,
  updateAdminGroupMember: api.change,
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: api.error } }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
const connection = {
  baseUrl: 'http://localhost:1933',
  accountId: 'acme',
  userId: 'root',
  apiKey: 'admin-key',
}
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <UserGroups connection={connection} />
    </QueryClientProvider>,
  )
  return userEvent.setup()
}
beforeEach(() => {
  vi.resetAllMocks()
  api.groups.mockResolvedValue([
    { group_id: 'engineering', member_count: 1 },
    { group_id: 'empty', member_count: 0 },
  ])
  api.members.mockResolvedValue({ group_id: 'engineering', members: ['alice'] })
  api.users.mockResolvedValue({
    users: [{ userId: 'alice' }, { userId: 'bob' }],
    total: 2,
  })
  api.create.mockResolvedValue({ group_id: 'new', member_count: 0 })
  api.remove.mockResolvedValue({ deleted: true })
  api.change.mockResolvedValue({ added: true })
})
afterEach(cleanup)

it('creates a trimmed group ID using the management connection and refreshes the list', async () => {
  const user = mount()
  await screen.findByText('engineering')
  await user.click(screen.getByRole('button', { name: 'groups.create' }))
  const dialog = screen.getByRole('dialog')
  await user.type(within(dialog).getByLabelText('groups.id'), ' new ')
  await user.click(
    within(dialog).getByRole('button', { name: 'groups.create' }),
  )
  await waitFor(() =>
    expect(api.create).toHaveBeenCalledWith(connection, 'new'),
  )
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(api.groups.mock.calls.length).toBeGreaterThan(1)
})

it('blocks nonempty deletion and requires confirmation for empty groups', async () => {
  const user = mount()
  const populated = (await screen.findByText('engineering')).closest('tr')!
  expect(
    within(populated)
      .getByRole('button', { name: 'groups.delete' })
      .hasAttribute('disabled'),
  ).toBe(true)
  const empty = screen.getByText('empty').closest('tr')!
  await user.click(within(empty).getByRole('button', { name: 'groups.delete' }))
  expect(api.remove).not.toHaveBeenCalled()
  await user.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'groups.delete',
    }),
  )
  await waitFor(() =>
    expect(api.remove).toHaveBeenCalledWith(connection, 'empty'),
  )
})

it('adds and removes members, disables duplicate additions and searches account users', async () => {
  const user = mount()
  await screen.findByText('engineering')
  await user.click(
    within(screen.getByText('engineering').closest('tr')!).getByRole('button', {
      name: 'groups.manage',
    }),
  )
  await screen.findByText('bob')
  const aliceCandidate = screen.getAllByText('alice')[1].closest('li')!
  expect(
    within(aliceCandidate)
      .getByRole('button', { name: 'groups.add' })
      .hasAttribute('disabled'),
  ).toBe(true)
  await user.click(
    within(screen.getByText('bob').closest('li')!).getByRole('button', {
      name: 'groups.add',
    }),
  )
  await waitFor(() =>
    expect(api.change).toHaveBeenCalledWith(
      connection,
      'engineering',
      'bob',
      true,
    ),
  )
  await waitFor(() =>
    expect(
      screen
        .getByRole('button', { name: 'groups.remove' })
        .hasAttribute('disabled'),
    ).toBe(false),
  )
  await user.click(screen.getByRole('button', { name: 'groups.remove' }))
  await waitFor(() =>
    expect(api.change).toHaveBeenCalledWith(
      connection,
      'engineering',
      'alice',
      false,
    ),
  )
  await user.type(screen.getByLabelText('groups.searchUsers'), 'bob')
  await waitFor(() =>
    expect(api.users).toHaveBeenLastCalledWith(connection, 'acme', {
      page: 1,
      pageSize: 20,
      search: 'bob',
    }),
  )
})

it('keeps a failed deletion open and refreshes potentially stale member counts', async () => {
  api.remove.mockRejectedValue(new Error('Group must be empty before deletion'))
  const user = mount()
  await screen.findByText('empty')
  await user.click(
    within(screen.getByText('empty').closest('tr')!).getByRole('button', {
      name: 'groups.delete',
    }),
  )
  await user.click(
    within(screen.getByRole('alertdialog')).getByRole('button', {
      name: 'groups.delete',
    }),
  )
  await waitFor(() => expect(api.error).toHaveBeenCalled())
  expect(screen.getByRole('alertdialog')).toBeTruthy()
  expect(api.groups.mock.calls.length).toBeGreaterThan(1)
})
