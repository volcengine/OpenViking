import { describe, expect, it } from 'vitest'

import {
  canEditStudioConfiguration,
  canManageStudioGateway,
  resolveStudioManagementCapabilities,
} from '#/features/connection/lib/studio-permissions'

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

describe('new management feature permissions', () => {
  const input = {
    serviceProvider: 'opensource' as const,
    hasControlCredential: true,
    isRoleLoading: false,
    role: 'root' as const,
    serverMode: 'api_key' as const,
  }
  it('allows trusted Gateway access while configuration requires authenticated trusted ROOT', () => {
    expect(canManageStudioGateway(input)).toBe(true)
    expect(canManageStudioGateway({ ...input, serverMode: 'trusted' })).toBe(
      true,
    )
    expect(
      canEditStudioConfiguration({ ...input, serverMode: 'trusted' }),
    ).toBe(true)
    expect(canEditStudioConfiguration({ ...input, role: 'admin' })).toBe(false)
  })
  it('allows ordinary API-key users and development mode to query Gateway access', () => {
    expect(
      canManageStudioGateway({
        ...input,
        role: 'user',
        hasControlCredential: false,
      }),
    ).toBe(true)
    expect(
      canManageStudioGateway({
        ...input,
        serverMode: 'dev',
        role: 'unknown',
        hasControlCredential: false,
      }),
    ).toBe(true)
    expect(canManageStudioGateway({ ...input, isRoleLoading: true })).toBe(
      false,
    )
  })
  it.each(['volcengine', 'unknown'] as const)(
    'never grants %s connections native management',
    (serviceProvider) => {
      expect(canManageStudioGateway({ ...input, serviceProvider })).toBe(false)
      expect(canEditStudioConfiguration({ ...input, serviceProvider })).toBe(
        false,
      )
    },
  )
})

it('exposes local development service configuration without nonexistent account membership management', () => { const input = { serverMode: 'dev' as const, role: 'root' as const, isRoleLoading: false, hasControlCredential: false }; expect(canEditStudioConfiguration(input)).toBe(true); expect(resolveStudioManagementCapabilities(input)).toEqual({ canManageAccounts: false, canManageUsers: false }) })
