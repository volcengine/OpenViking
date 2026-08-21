import { describe, expect, it } from 'vitest'

import { resolveStudioManagementCapabilities } from './studio-permissions'

describe('resolveStudioManagementCapabilities', () => {
  it('allows a validated root control credential to manage accounts and users', () => {
    expect(
      resolveStudioManagementCapabilities({
        hasControlCredential: true,
        isRoleLoading: false,
        role: 'root',
        serverMode: 'api_key',
      }),
    ).toEqual({
      canManageAccounts: true,
      canManageUsers: true,
    })
  })

  it('allows a verified trusted Root key to manage accounts and users', () => {
    expect(
      resolveStudioManagementCapabilities({
        hasControlCredential: true,
        isRoleLoading: false,
        role: 'root',
        serverMode: 'trusted',
      }),
    ).toEqual({
      canManageAccounts: true,
      canManageUsers: true,
    })
  })

  it('limits account admins to user management', () => {
    expect(
      resolveStudioManagementCapabilities({
        hasControlCredential: true,
        isRoleLoading: false,
        role: 'admin',
        serverMode: 'api_key',
      }),
    ).toEqual({
      canManageAccounts: false,
      canManageUsers: true,
    })
  })

  it('does not expose management to ordinary users', () => {
    expect(
      resolveStudioManagementCapabilities({
        hasControlCredential: true,
        isRoleLoading: false,
        role: 'user',
        serverMode: 'api_key',
      }),
    ).toEqual({
      canManageAccounts: false,
      canManageUsers: false,
    })
  })

  it('does not infer permissions from an unverified input value', () => {
    expect(
      resolveStudioManagementCapabilities({
        hasControlCredential: true,
        isRoleLoading: true,
        role: 'unknown',
        serverMode: 'api_key',
      }),
    ).toEqual({
      canManageAccounts: false,
      canManageUsers: false,
    })
  })
})
