import { useCallback, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { UseQueryOptions } from '@tanstack/react-query'

import { useAppConnection } from '#/hooks/use-app-connection'
import type { AdminConnection } from '#/lib/admin'
import { resolveStudioManagementCapabilities } from '#/lib/studio-permissions'

import {
  getConnectionInfo,
  getOverview,
  listKeyUsers,
  listKeys,
  listLogs,
  listProfiles,
  listTools,
  listUpstreams,
} from './api'
import type {
  ConnectionInfo,
  GatewayError,
  GatewayKey,
  GatewayTool,
  KeyUser,
  LogRecord,
  Overview,
  Profile,
  Upstream,
} from './api'

/** Cached gateway data, invalidated per resource after a change. */
export type GatewayResource =
  | 'connection'
  | 'overview'
  | 'logs'
  | 'upstreams'
  | 'profiles'
  | 'tools'
  | 'keys'
  | 'users'

/** Whose gateway data a query holds: server, account and a hash of the admin key. */
export type GatewayScope = readonly [string, string, string]

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
): GatewayScope {
  return [baseUrl, accountId, adminApiKey ? hashSecret(adminApiKey) : 'none']
}

/** Query key `['context-gateway', scope, resource, ...params]`. */
export function gatewayQueryKey(
  scope: GatewayScope,
  resource?: GatewayResource,
  ...params: unknown[]
) {
  return resource
    ? (['context-gateway', scope, resource, ...params] as const)
    : (['context-gateway', scope] as const)
}

/**
 * Access and connection for gateway management. `connection` uses the
 * account admin key; `invalidate()` refreshes the listed resources, or all
 * gateway data when called without arguments.
 */
export function useGateway() {
  const { connection, connectionRole, isConnectionRoleLoading, serverMode } =
    useAppConnection()
  const { baseUrl, accountId, userId, adminApiKey } = connection
  const { canManageUsers } = resolveStudioManagementCapabilities({
    hasControlCredential: Boolean(adminApiKey.trim()),
    isRoleLoading: isConnectionRoleLoading,
    role: connectionRole,
    serverMode,
  })
  const admin = useMemo<AdminConnection>(
    () => ({ baseUrl, accountId, userId, apiKey: adminApiKey }),
    [baseUrl, accountId, userId, adminApiKey],
  )
  const scope = useMemo(
    () => gatewayScope(baseUrl, accountId, adminApiKey),
    [baseUrl, accountId, adminApiKey],
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
    allowed: canManageUsers,
    /** OpenViking runs in development mode, where Studio can't manage the gateway. */
    devMode: serverMode === 'dev',
    isRoleLoading: isConnectionRoleLoading,
    /** Role of the admin key; `admin` or `root` whenever `allowed`. */
    role: connectionRole,
    connection: admin,
    scope,
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
  load: (connection: AdminConnection) => Promise<T>,
  options: GatewayQueryOptions<T> = {},
) {
  const { allowed, connection, scope } = useGateway()
  return useQuery<T, GatewayError>({
    ...options,
    queryKey: gatewayQueryKey(scope, resource, ...params),
    queryFn: () => load(connection),
    enabled: allowed && options.enabled !== false,
  })
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
