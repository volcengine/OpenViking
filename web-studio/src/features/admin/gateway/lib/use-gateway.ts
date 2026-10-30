import i18n from '#/i18n/core'
import type { UseQueryOptions } from '@tanstack/react-query'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useMemo } from 'react'

import { useManagementKey } from '#/features/admin/gateway/lib/management-key'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'

import type {
  ConnectionInfo,
  GatewayAccess,
  GatewayConnection,
  GatewayError,
  GatewayKey,
  GatewayTool,
  KeyUser,
  LogRecord,
  Overview,
  Profile,
  Upstream,
} from '#/features/admin/gateway/lib/api'
import {
  getAccess,
  getConnectionInfo,
  getOverview,
  listKeyUsers,
  listKeys,
  listLogs,
  listProfiles,
  listTools,
  listUpstreams,
} from '#/features/admin/gateway/lib/api'

/** Cached gateway data, invalidated per resource after a change. */
export type GatewayResource =
  | 'access'
  | 'connection'
  | 'overview'
  | 'logs'
  | 'upstreams'
  | 'profiles'
  | 'tools'
  | 'keys'
  | 'users'

/** Cache isolation by server, account and fingerprints of both credentials. */
export type GatewayScope = readonly [string, string, string, string]

function hashSecret(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(36)
}

export function gatewayScope(
  baseUrl: string,
  accountId: string,
  adminApiKey: string,
  managementKey = '',
): GatewayScope {
  return [
    baseUrl,
    accountId,
    adminApiKey ? hashSecret(adminApiKey) : 'none',
    managementKey ? hashSecret(managementKey) : 'none',
  ]
}

/** Query key `['gateway', scope, resource, ...params]`. */
export function gatewayQueryKey(
  scope: GatewayScope,
  resource?: GatewayResource,
  ...params: unknown[]
) {
  return resource
    ? (['gateway', scope, resource, ...params] as const)
    : (['gateway', scope] as const)
}

/**
 * Access and connection for gateway management. `connection` uses the
 * configured OpenViking credential; `invalidate()` refreshes the listed resources, or all
 * gateway data when called without arguments.
 */
function useGatewayConnection() {
  const { connection, isConnectionRoleLoading, serverMode } = useAppConnection()
  const service = useStudioService()
  const { baseUrl, accountId, userId } = connection
  const apiKey = connection.adminApiKey || connection.apiKey
  const managementKey = useManagementKey(baseUrl)
  const admin = useMemo<GatewayConnection>(
    () => ({ baseUrl, accountId, userId, apiKey, managementKey }),
    [baseUrl, accountId, userId, apiKey, managementKey],
  )
  const scope = useMemo(
    () => gatewayScope(baseUrl, accountId, apiKey, managementKey),
    [baseUrl, accountId, apiKey, managementKey],
  )
  const queryClient = useQueryClient()
  const invalidate = useCallback(
    async (...resources: GatewayResource[]) => {
      const keys = resources.length
        ? resources.map((resource) => gatewayQueryKey(scope, resource))
        : [gatewayQueryKey(scope)]
      await Promise.all(
        keys.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
      )
    },
    [queryClient, scope],
  )
  return {
    /**
     * Dev mode needs no credential; trusted mode lets /access explain that
     * the gateway doesn't support it.
     */
    allowed:
      service.ready &&
      service.provider === 'opensource' &&
      (serverMode === 'dev' ||
        serverMode === 'trusted' ||
        Boolean(apiKey.trim())),
    /** Local development authenticates as default/default without a credential. */
    devMode: serverMode === 'dev',
    isRoleLoading: isConnectionRoleLoading,
    connection: admin,
    scope,
    managementKey,
    invalidate,
  }
}

/** Extra `useQuery` options a page may set (polling, placeholders…). */
export type GatewayQueryOptions<T> = Omit<
  UseQueryOptions<T, GatewayError>,
  'queryKey' | 'queryFn'
>

function useGatewayQuery<T>(
  resource: GatewayResource,
  params: unknown[],
  load: (connection: GatewayConnection) => Promise<T>,
  options: GatewayQueryOptions<T> = {},
) {
  const { allowed, connection, scope } = useGatewayConnection()
  return useQuery<T, GatewayError>({
    ...options,
    queryKey: gatewayQueryKey(scope, resource, ...params),
    queryFn: () => {
      if (!allowed) throw new Error(i18n.t('studio:errors.gatewayUnavailable'))
      return load(connection)
    },
    enabled: allowed && options.enabled !== false,
  })
}

export const useAccess = (options?: GatewayQueryOptions<GatewayAccess>) =>
  useGatewayQuery('access', [], getAccess, {
    retry: false,
    retryOnMount: false,
    staleTime: 30_000,
    ...options,
  })

/** Connection plus the permissions /access grants; pages read only these. */
export function useGateway() {
  const gateway = useGatewayConnection()
  const accessQuery = useAccess()
  const access = accessQuery.data
  return {
    ...gateway,
    access,
    accessQuery,
    /** Upstreams and profiles can be viewed and tested, not changed. */
    readOnly: !access?.can_write_objects,
    canIssueForOthers: Boolean(access?.can_issue_for_others),
  }
}

/** Gateway address for clients (also tells whether the gateway is reachable). */
export const useConnectionInfo = (
  options?: GatewayQueryOptions<ConnectionInfo>,
) => useGatewayQuery('connection', [], getConnectionInfo, options)

export const useOverview = (options?: GatewayQueryOptions<Overview>) =>
  useGatewayQuery('overview', [], getOverview, options)

/** Newest-first request log, up to `limit` (≤ 1,000) records. */
export const useLogs = (
  limit: number,
  options?: GatewayQueryOptions<LogRecord[]>,
) =>
  useGatewayQuery(
    'logs',
    [limit],
    (connection) => listLogs(connection, limit),
    options,
  )

export const useUpstreams = (options?: GatewayQueryOptions<Upstream[]>) =>
  useGatewayQuery('upstreams', [], listUpstreams, options)

export const useProfiles = (options?: GatewayQueryOptions<Profile[]>) =>
  useGatewayQuery('profiles', [], listProfiles, options)

export const useTools = (options?: GatewayQueryOptions<GatewayTool[]>) =>
  useGatewayQuery('tools', [], listTools, options)

export const useKeys = (options?: GatewayQueryOptions<GatewayKey[]>) =>
  useGatewayQuery('keys', [], listKeys, options)

/** Users of this account a key can act as, and whether the server can read their key. */
export const useKeyUsers = (options?: GatewayQueryOptions<KeyUser[]>) =>
  useGatewayQuery('users', [], listKeyUsers, options)
