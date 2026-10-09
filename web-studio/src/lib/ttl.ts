import { createOvClient, getOvResult } from '#/lib/ov-client'
import type { ConnectionDraft } from '#/hooks/use-app-connection'

export type TtlPolicy = {
  mode?: 'inherit' | 'disabled' | 'days' | 'absolute'
  ttl_days?: number
  ttl_absolute?: number
}
export const ttlScopes = [
  'global',
  'user_events',
  'peer_events',
  'sessions',
] as const
export type TtlScope = (typeof ttlScopes)[number]
export type TtlConfig = Partial<Record<TtlScope, TtlPolicy>> & {
  directories?: Record<string, TtlPolicy>
}
type AccountConfig = { settings: { ttl?: TtlConfig } }
export type TtlReport = {
  uri: string
  expires_at: string | null
  policy?: TtlPolicy
  effective_policy?: TtlPolicy
}

export function ttlRootForPath(uri: string): string | undefined {
  if (
    /[?#\\]/.test(uri) ||
    uri.split('/').some((part) => part === '.' || part === '..')
  )
    return
  return uri.match(
    /^viking:\/\/user\/[^/]+\/(?:(?:peers\/[^/]+\/)?memories\/events|sessions)(?=\/|$)/,
  )?.[0]
}

export function isTtlRoot(uri: string) {
  const normalized = uri.replace(/\/+$/, '')
  return Boolean(normalized && ttlRootForPath(normalized) === normalized)
}

export function ttlPolicyPatch(target: string, policy: TtlPolicy | null) {
  if (ttlScopes.includes(target as TtlScope)) return { [target]: policy }
  if (!isTtlRoot(target))
    throw new Error('TTL configuration requires an events or sessions root URI')
  return { directories: { [target.replace(/\/+$/, '')]: policy } }
}

// Capture this connection: requests must not follow a later account/identity switch.
export function createTtlApi(
  connection: ConnectionDraft,
  identityHeaders: boolean,
  accountAdmin = false,
) {
  const { client } = createOvClient({
    baseUrl: connection.baseUrl,
    connection: {
      ...connection,
      apiKey: accountAdmin ? connection.adminApiKey : connection.apiKey,
      identityHeaders,
    },
  })
  const url = '/api/v1/admin/accounts/{account_id}/configuration'
  const path = { account_id: connection.accountId }
  return {
    async getConfig() {
      return (
        (await getOvResult<AccountConfig>(client.get({ url, path }))).settings
          .ttl ?? {}
      )
    },
    async setPolicy(target: string, policy: TtlPolicy | null) {
      const result = await getOvResult<AccountConfig>(
        client.patch({
          url,
          path,
          headers: { 'Content-Type': 'application/json' },
          body: { settings: { ttl: ttlPolicyPatch(target, policy) } },
        }),
      )
      return result.settings.ttl ?? {}
    },
    get: (uri: string) =>
      getOvResult<TtlReport>(
        client.get({ url: '/api/v1/content/ttl', query: { uri } }),
      ),
  }
}
