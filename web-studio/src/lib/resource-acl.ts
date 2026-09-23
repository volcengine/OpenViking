import { createOvClient, getOvResult } from '#/lib/ov-client'
import type { ConnectionDraft } from '#/hooks/use-app-connection'
import type { FSListResult } from '@ov-server/api/v1/fs'

export type AclLevel = 'read' | 'write' | 'manage'
export type AclEntry = { principal: string; level: AclLevel }
export type AclReport = {
  uri: string
  acl_mode: 'none' | 'inherit' | 'restricted'
  direct_entries: AclEntry[]
  inherited_entries: AclEntry[]
  effective_entries: AclEntry[]
}
export type AclChange =
  | { kind: 'grant'; principal: string; level: AclLevel }
  | { kind: 'revoke'; principal: string }
  | { kind: 'mode'; mode: 'inherit' | 'restricted' }
  | { kind: 'reset' }

type AccountConfig = { settings: { acl?: { enabled?: boolean } } }

export function isSharedAclTarget(uri: string) {
  const parts = uri.replace(/\/+$/, '').split('/')
  return (
    uri.startsWith('viking://resources/') &&
    parts.length > 3 &&
    parts.slice(3).every((part) => part && part !== '.' && part !== '..')
  )
}

// Capture both identities so an in-flight operation never follows a later UI switch.
export function createResourceAclApi(
  connection: ConnectionDraft,
  trusted: boolean,
  useAccountAdminCredential = false,
) {
  const { client } = createOvClient({
    baseUrl: connection.baseUrl,
    connection: {
      ...connection,
      apiKey: useAccountAdminCredential
        ? connection.adminApiKey
        : connection.apiKey,
      identityHeaders: trusted,
    },
  })
  const configuration = '/api/v1/admin/accounts/{account_id}/configuration'
  const path = { account_id: connection.accountId }
  return {
    async getEnabled() {
      const result = await getOvResult<AccountConfig>(
        client.get({ url: configuration, path }),
      )
      return result.settings.acl?.enabled === true
    },
    async setEnabled(enabled: boolean) {
      const result = await getOvResult<AccountConfig>(
        client.patch({
          url: configuration,
          path,
          headers: { 'Content-Type': 'application/json' },
          body: { settings: { acl: { enabled } } },
        }),
      )
      return result.settings.acl?.enabled === true
    },
    get: (uri: string) =>
      getOvResult<AclReport>(
        client.get({ url: '/api/v1/acl', query: { uri } }),
      ),
    listDirectory: (uri: string) =>
      getOvResult<FSListResult>(
        client.get({
          url: '/api/v1/fs/ls',
          query: { uri, show_all_hidden: false },
        }),
      ),
    change(uri: string, change: AclChange) {
      const headers = { 'Content-Type': 'application/json' }
      if (change.kind === 'reset')
        return getOvResult<AclReport>(
          client.delete({ url: '/api/v1/acl', query: { uri } }),
        )
      if (change.kind === 'mode')
        return getOvResult<AclReport>(
          client.put({
            url: '/api/v1/acl',
            headers,
            body: { uri, acl_mode: change.mode },
          }),
        )
      return getOvResult<AclReport>(
        client.post({
          url: `/api/v1/acl/${change.kind}`,
          headers,
          body: {
            uri,
            principal: change.principal,
            ...(change.kind === 'grant' ? { level: change.level } : {}),
          },
        }),
      )
    },
  }
}
