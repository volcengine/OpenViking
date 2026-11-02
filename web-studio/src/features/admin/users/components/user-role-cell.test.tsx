// @vitest-environment jsdom
import { UserRoleCell } from '#/features/admin/users/components/user-role-cell'
import { resolveStudioManagementCapabilities } from '#/features/connection/lib/studio-permissions'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(cleanup)
it.each(['root', 'admin'] as const)(
  'lets an authorized %s change a member between User and Admin, with no Root grant option',
  async (role) => {
    const permissions = resolveStudioManagementCapabilities({
      role,
      hasControlCredential: true,
      isRoleLoading: false,
      serverMode: 'api_key',
    })
    const onChange = vi.fn()
    render(
      <UserRoleCell
        userId="alice"
        role="user"
        editable={permissions.canManageUsers}
        pending={false}
        onChange={onChange}
      />,
    )
    const user = userEvent.setup()
    await user.tab()
    await user.keyboard('[ArrowDown]')
    const adminOption = await screen.findByRole('option', {
      name: 'roles.admin',
    })
    expect(screen.queryByRole('option', { name: 'roles.root' })).toBeNull()
    await user.click(adminOption)
    expect(onChange).toHaveBeenCalledWith('admin')
  },
)
it('does not expose role changes to a regular User', () => {
  const permissions = resolveStudioManagementCapabilities({
    role: 'user',
    hasControlCredential: true,
    isRoleLoading: false,
    serverMode: 'api_key',
  })
  render(
    <UserRoleCell
      userId="alice"
      role="user"
      editable={permissions.canManageUsers}
      pending={false}
      onChange={vi.fn()}
    />,
  )
  expect(screen.queryByRole('combobox')).toBeNull()
  expect(screen.getByText('roles.user')).toBeTruthy()
})
it('does not treat a legacy Root member as an assignable account role', () => {
  render(
    <UserRoleCell
      userId="legacy"
      role="root"
      editable
      pending={false}
      onChange={vi.fn()}
    />,
  )
  expect(screen.queryByRole('combobox')).toBeNull()
})
