import { AxiosHeaders } from 'axios'
import type { InternalAxiosRequestConfig } from 'axios'

type AccountCredential = {
  apiKey: string
  userId: string
  rootOnlyUserIds: string[]
}
type ManagementCredentials = {
  baseUrl: string
  rootApiKey: string
  resolveAccount: (accountId: string) => Promise<AccountCredential | null>
  invalidateAccount: (accountId: string) => void
}
let management: ManagementCredentials | undefined

/** The connection provider owns the lifetime of Root and its tenant credentials. */
export function configureManagementCredentials(next: ManagementCredentials) {
  management = next
  return () => {
    if (management === next) management = undefined
  }
}

export function hasRootManagementCredential(baseUrl: string, apiKey: string) {
  if (!management) return false
  return (
    management.baseUrl.replace(/\/+$/, '') === baseUrl.replace(/\/+$/, '') &&
    management.rootApiKey === apiKey
  )
}

function accountOperation(config: InternalAxiosRequestConfig, baseUrl: string) {
  const base = new URL(baseUrl)
  const url = new URL(config.url ?? '', config.baseURL || baseUrl)
  const prefix = base.pathname.replace(/\/+$/, '')
  if (url.origin !== base.origin || !url.pathname.startsWith(`${prefix}/`))
    return null
  const pathname = url.pathname.slice(prefix.length)
  if (pathname.startsWith('/gateway/admin/')) {
    const accountId =
      config.params?.account_id ?? url.searchParams.get('account_id')
    if (typeof accountId !== 'string' || !accountId) return null
    return {
      url,
      accountId,
      resource: 'gateway',
      target: pathname.slice('/gateway/admin/'.length),
    }
  }
  const match =
    /^\/api\/v1\/admin\/accounts\/([^/]+)\/(users|groups|configuration|settings|memory-templates)(?:\/(.*))?$/.exec(
      pathname,
    )
  if (!match) return null
  const [, account, resource, target] = match
  return { url, accountId: decodeURIComponent(account), resource, target }
}

/** Select by the endpoint's permission contract, never by retrying arbitrary 403s. */
export async function routeManagementCredential(
  config: InternalAxiosRequestConfig,
) {
  const current = management
  if (!current) return config
  const headers = AxiosHeaders.from(config.headers)
  if (
    headers.get('X-API-Key') !== current.rootApiKey ||
    headers.has('Authorization')
  )
    return config
  const operation = accountOperation(config, current.baseUrl)
  if (!operation) return config
  const method = config.method?.toUpperCase()
  const { accountId, resource, target, url } = operation
  // Revealing every member's credential requires Root. Metadata uses Admin.
  const includeCredentials =
    config.params?.include_credentials ??
    url.searchParams.get('include_credentials')
  if (
    resource === 'users' &&
    !target &&
    method === 'GET' &&
    includeCredentials !== false &&
    includeCredentials !== 'false'
  )
    return config
  const gatewayManagementKey = headers.get(
    'X-OpenViking-Gateway-Management-Key',
  )
  if (
    resource === 'gateway' &&
    (target === 'account/data' ||
      (!gatewayManagementKey &&
        (target === 'access' || target === 'upstreams/import-vlm')))
  )
    return config
  let body = config.data
  if (typeof body === 'string') {
    try {
      body = JSON.parse(body)
    } catch {
      return config
    }
  }
  if (
    resource === 'users' &&
    typeof body?.role === 'string' &&
    body.role.toLowerCase() === 'global'
  )
    return config
  const credential = await current.resolveAccount(accountId)
  if (!credential) return config
  if (
    resource === 'gateway' &&
    target === 'keys' &&
    method === 'POST' &&
    !body?.openviking_key &&
    body?.user_id &&
    body.user_id !== credential.userId
  )
    return config
  const targetUser = target
    ? decodeURIComponent(target.split('/')[0])
    : undefined
  if (
    resource === 'users' &&
    targetUser &&
    credential.rootOnlyUserIds.includes(targetUser)
  )
    return config
  headers.set('X-API-Key', credential.apiKey)
  config.headers = headers
  return config
}

export function invalidateManagementCredential(
  config: InternalAxiosRequestConfig,
  rejected = false,
) {
  const current = management
  if (!current) return
  const operation = accountOperation(config, current.baseUrl)
  if (
    operation &&
    (rejected ||
      (operation.resource === 'users' &&
        config.method?.toUpperCase() !== 'GET'))
  ) {
    current.invalidateAccount(operation.accountId)
  }
}
