import { describe, expect, it } from 'vitest'
import { adminConsoleHeaders } from './admin-console-headers'

const connection = {
  accountId: 'team',
  adminApiKey: 'management',
  apiKey: 'workbench',
  baseUrl: 'http://localhost:1933',
  userId: 'alice',
}

describe('adminConsoleHeaders', () => {
  it('selects the account admin key when a workbench key is also present', () => {
    expect(adminConsoleHeaders(connection, 'admin', 'api_key')).toEqual({
      'X-API-Key': 'management',
    })
  })

  it('rejects a non-default account for an unbound Root key', () => {
    expect(adminConsoleHeaders(connection, 'root', 'api_key')).toBeNull()
    expect(
      adminConsoleHeaders(
        { ...connection, accountId: 'default' },
        'root',
        'api_key',
      ),
    ).toEqual({ 'X-API-Key': 'management' })
  })

  it('asserts admin for the selected identity in trusted mode', () => {
    expect(adminConsoleHeaders(connection, 'root', 'trusted')).toEqual({
      'X-API-Key': 'management',
      'X-OpenViking-Role': 'admin',
    })
    expect(
      adminConsoleHeaders({ ...connection, userId: '' }, 'root', 'trusted'),
    ).toBeNull()
  })
})
