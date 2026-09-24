import axios from 'axios'
import { fetchServerHealth } from '#/hooks/use-server-mode'

import { createClient } from '#/gen/ov-client/client'
import {
  deleteAdminAccountByAccountId,
  deleteAdminAccountIdUserByUserId,
  getAdminAccountIdUsers,
  getAdminAccounts,
  getOvResult,
  postAdminAccountIdUserIdKey,
  postAdminAccounts,
  putAdminAccountIdUserIdRole,
} from '#/lib/ov-client'

import type { UserMemoryPolicy } from './user-memory-policy'

export type AdminUserRole = 'admin' | 'root' | 'user'

export type AdminConnection = {
  accountId: string
  apiKey: string
  baseUrl: string
  userId: string
}

export type AdminAccount = {
  accountId: string
  createdAt?: string
  userCount: number
}

export type AdminUser = {
  accountId: string
  apiKey?: string
  keyPrefix?: string
  role: string
  userId: string
}

export type CreateAccountInput = {
  accountId: string
  adminUserId: string
}

export type CreateUserInput = {
  memoryPolicy?: UserMemoryPolicy
  accountId: string
  role: string
  userId: string
}

export type KeyResult = {
  accountId?: string
  apiKey: string
  userId?: string
}

export type UpdateUserRoleInput = {
  accountId: string
  role: AdminUserRole
  userId: string
}

export type ProbeState = 'ok' | 'error' | 'skipped'

export type CapabilityDetailCode =
  | 'accountAdminAvailable'
  | 'adminModeRequired'
  | 'controlKeyRequired'
  | 'dataKeyRequired'
  | 'rootAvailable'
  | 'tenantDataAvailable'
  | 'trustedIdentityRequired'

export type CapabilityProbeResult = {
  detail?: string
  detailCode?: CapabilityDetailCode
  errorCode?: string
  statusCode?: number
  state: ProbeState
}

export type StudioConnectionProbe = {
  admin: CapabilityProbeResult
  data: CapabilityProbeResult
  rootApiKeyRequired?: boolean
}

export type ProbeConnectionInput = {
  accountId: string
  adminApiKey: string
  apiKey: string
  baseUrl: string
  serverMode: 'api_key' | 'trusted' | 'dev' | 'checking' | 'offline'
  userId: string
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

function asNumber(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.trim().replace(/\/+$/, '')
}

function setOptionalHeader(
  headers: Record<string, string>,
  name: string,
  value: string,
): void {
  const trimmed = value.trim()
  if (trimmed) {
    headers[name] = trimmed
  }
}

function createAdminClient(connection: AdminConnection) {
  const headers: Record<string, string> = {
    Accept: 'application/json',
  }
  setOptionalHeader(headers, 'X-API-Key', connection.apiKey)

  return createClient({
    axios: axios.create(),
    baseURL: normalizeBaseUrl(connection.baseUrl),
    headers,
    throwOnError: true,
  })
}

function getErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data
    if (isRecord(data) && isRecord(data.error)) {
      const message = asString(data.error.message)
      if (message) {
        return message
      }
    }

    if (error.response?.status) {
      return `HTTP ${error.response.status}`
    }
  }

  return error instanceof Error ? error.message : String(error)
}

function createProbeClient(baseUrl: string) {
  return axios.create({
    baseURL: normalizeBaseUrl(baseUrl),
    headers: {
      Accept: 'application/json',
    },
  })
}

function setApiKeyHeader(
  headers: Record<string, string>,
  apiKey: string,
): void {
  setOptionalHeader(headers, 'X-API-Key', apiKey)
}

function probeError(error: unknown): CapabilityProbeResult {
  const response = axios.isAxiosError(error) ? error.response : undefined
  const payload: unknown = response?.data
  return {
    detail: getErrorMessage(error),
    errorCode:
      isRecord(payload) && isRecord(payload.error)
        ? asString(payload.error.code)
        : undefined,
    statusCode: response?.status,
    state: 'error',
  }
}

async function probeAdminAccess(
  input: ProbeConnectionInput,
): Promise<CapabilityProbeResult> {
  if (input.serverMode === 'checking' || input.serverMode === 'offline') {
    return { state: 'skipped' }
  }
  if (input.serverMode === 'dev') {
    return {
      detailCode: 'adminModeRequired',
      state: 'skipped',
    }
  }

  // The Root API key is the only control-plane credential. The User API key is
  // never promoted to admin access, so management stays gated behind the root
  // or account-admin key alone.
  const controlKey = input.adminApiKey.trim()
  if (!controlKey) {
    return {
      detailCode: 'controlKeyRequired',
      state: 'skipped',
    }
  }

  const client = createProbeClient(input.baseUrl)
  const headers: Record<string, string> = {}
  setApiKeyHeader(headers, controlKey)

  try {
    await client.get('/api/v1/admin/accounts', { headers })
    return {
      detailCode: 'rootAvailable',
      state: 'ok',
    }
  } catch (accountsError) {
    if (
      !axios.isAxiosError(accountsError) ||
      accountsError.response?.status !== 403 ||
      !input.accountId
    ) {
      return probeError(accountsError)
    }

    try {
      const accountId = encodeURIComponent(input.accountId)
      await client.get(`/api/v1/admin/accounts/${accountId}/users`, {
        headers,
        params: {
          limit: 1,
        },
      })
      return {
        detailCode: 'accountAdminAvailable',
        state: 'ok',
      }
    } catch (usersError) {
      return probeError(usersError)
    }
  }
}

async function probeDataAccess(
  input: ProbeConnectionInput,
): Promise<CapabilityProbeResult> {
  if (input.serverMode === 'checking' || input.serverMode === 'offline') {
    return { state: 'skipped' }
  }
  if (input.serverMode === 'api_key' && !input.apiKey) {
    return {
      detailCode: 'dataKeyRequired',
      state: 'skipped',
    }
  }
  if (
    input.serverMode === 'trusted' &&
    (!input.accountId.trim() || !input.userId.trim())
  ) {
    return {
      detailCode: 'trustedIdentityRequired',
      state: 'skipped',
    }
  }

  const client = createProbeClient(input.baseUrl)
  const headers: Record<string, string> = {}
  setApiKeyHeader(
    headers,
    input.apiKey || (input.serverMode === 'trusted' ? input.adminApiKey : ''),
  )

  if (input.serverMode === 'trusted') {
    setOptionalHeader(headers, 'X-OpenViking-Account', input.accountId)
    setOptionalHeader(headers, 'X-OpenViking-User', input.userId)
  }

  try {
    await client.get('/api/v1/fs/ls', {
      headers,
      params: {
        node_limit: 1,
        output: 'agent',
        uri: 'viking://',
      },
    })
    return {
      detailCode: 'tenantDataAvailable',
      state: 'ok',
    }
  } catch (error) {
    return probeError(error)
  }
}

export async function probeStudioConnection(
  input: ProbeConnectionInput,
): Promise<StudioConnectionProbe> {
  const headers: Record<string, string> = {}
  setApiKeyHeader(headers, input.adminApiKey || input.apiKey)
  setOptionalHeader(headers, 'X-OpenViking-Account', input.accountId)
  setOptionalHeader(headers, 'X-OpenViking-User', input.userId)
  const [admin, data, health] = await Promise.all([
    probeAdminAccess(input),
    probeDataAccess(input),
    Promise.allSettled(
      input.serverMode === 'trusted'
        ? [fetchServerHealth(input.baseUrl, headers)]
        : [],
    ),
  ])
  const metadata =
    health[0]?.status === 'fulfilled' ? health[0].value : undefined
  return {
    admin,
    data,
    rootApiKeyRequired:
      typeof metadata?.root_api_key_required === 'boolean'
        ? metadata.root_api_key_required
        : undefined,
  }
}

function normalizeAccount(value: unknown): AdminAccount | null {
  if (!isRecord(value) || value.status === 'deleting') {
    return null
  }

  const accountId = asString(value.account_id)
  if (!accountId) {
    return null
  }

  return {
    accountId,
    createdAt: asString(value.created_at),
    userCount: asNumber(value.user_count),
  }
}

function normalizeUser(accountId: string, value: unknown): AdminUser | null {
  if (!isRecord(value)) {
    return null
  }

  const userId = asString(value.user_id)
  if (!userId) {
    return null
  }

  return {
    accountId,
    apiKey: asString(value.api_key),
    keyPrefix: asString(value.key_prefix),
    role: asString(value.role) || 'user',
    userId,
  }
}

function normalizeKeyResult(value: unknown): KeyResult {
  if (!isRecord(value)) {
    return { apiKey: '' }
  }

  return {
    accountId: asString(value.account_id),
    apiKey: asString(value.user_key) || asString(value.api_key) || '',
    userId: asString(value.user_id) || asString(value.admin_user_id),
  }
}

export async function fetchAdminAccounts(
  connection: AdminConnection,
): Promise<AdminAccount[]> {
  const result = await getOvResult<unknown[]>(
    getAdminAccounts({
      client: createAdminClient(connection),
    }),
  )
  return result
    .map((item) => normalizeAccount(item))
    .filter((item): item is AdminAccount => Boolean(item))
}

export async function fetchAdminUsers(
  connection: AdminConnection,
  accountId: string,
): Promise<AdminUser[]> {
  const result = await getOvResult<unknown[]>(
    getAdminAccountIdUsers({
      client: createAdminClient(connection),
      path: {
        account_id: accountId,
      },
    }),
  )
  return result
    .map((item) => normalizeUser(accountId, item))
    .filter((item): item is AdminUser => Boolean(item))
}

export type AdminUserPage = {
  users: AdminUser[]
  total: number
  accountTotal: number
  managerCount: number
  keyCount: number
}

export async function fetchAdminUsersPage(
  connection: AdminConnection,
  accountId: string,
  options: { page: number; pageSize: number; search: string },
): Promise<AdminUserPage> {
  const result = await getOvResult<{
    users: unknown[]
    total: number
    account_total: number
    manager_count: number
    key_count: number
  }>(
    createAdminClient(connection).get({
      url: '/api/v1/admin/accounts/{account_id}/users',
      path: { account_id: accountId },
      query: {
        page: options.page,
        limit: options.pageSize,
        query: options.search.trim() || undefined,
        include_summary: true,
      },
    }),
  )
  return {
    users: result.users
      .map((item) => normalizeUser(accountId, item))
      .filter((item): item is AdminUser => Boolean(item)),
    total: result.total,
    accountTotal: result.account_total,
    managerCount: result.manager_count,
    keyCount: result.key_count,
  }
}

export async function createAdminAccount(
  connection: AdminConnection,
  input: CreateAccountInput,
): Promise<KeyResult> {
  const result = await getOvResult<unknown>(
    postAdminAccounts({
      body: {
        account_id: input.accountId,
        admin_user_id: input.adminUserId,
      },
      client: createAdminClient(connection),
    }),
  )
  return normalizeKeyResult(result)
}

export async function deleteAdminAccount(
  connection: AdminConnection,
  accountId: string,
): Promise<string> {
  const result = await getOvResult<{ task_id: string }>(
    deleteAdminAccountByAccountId({
      client: createAdminClient(connection),
      path: {
        account_id: accountId,
      },
    }),
  )
  return result.task_id
}

export async function createAdminUser(
  connection: AdminConnection,
  input: CreateUserInput,
): Promise<KeyResult> {
  const result = await getOvResult<unknown>(
    createAdminClient(connection).post({
      url: '/api/v1/admin/accounts/{account_id}/users',
      headers: { 'Content-Type': 'application/json' },
      body: {
        role: input.role,
        user_id: input.userId,
        ...(input.memoryPolicy
          ? { user_config: { memory_policy: input.memoryPolicy } }
          : {}),
      },
      path: {
        account_id: input.accountId,
      },
    }),
  )
  return normalizeKeyResult(result)
}

export async function regenerateAdminUserKey(
  connection: AdminConnection,
  accountId: string,
  userId: string,
): Promise<KeyResult> {
  const result = await getOvResult<unknown>(
    postAdminAccountIdUserIdKey({
      client: createAdminClient(connection),
      path: {
        account_id: accountId,
        user_id: userId,
      },
    }),
  )
  return normalizeKeyResult({
    ...(isRecord(result) ? result : {}),
    account_id: accountId,
    user_id: userId,
  })
}

export async function removeAdminUser(
  connection: AdminConnection,
  accountId: string,
  userId: string,
): Promise<void> {
  await getOvResult<unknown>(
    deleteAdminAccountIdUserByUserId({
      client: createAdminClient(connection),
      path: {
        account_id: accountId,
        user_id: userId,
      },
    }),
  )
}

export async function updateAdminUserRole(
  connection: AdminConnection,
  input: UpdateUserRoleInput,
): Promise<void> {
  await getOvResult<unknown>(
    putAdminAccountIdUserIdRole({
      body: {
        role: input.role,
      },
      client: createAdminClient(connection),
      path: {
        account_id: input.accountId,
        user_id: input.userId,
      },
    }),
  )
}

export type UserMemorySettings = { memory_policy: UserMemoryPolicy }

export async function fetchUserMemorySettings(
  connection: AdminConnection,
  accountId: string,
  userId: string,
): Promise<UserMemorySettings> {
  return getOvResult<UserMemorySettings>(
    createAdminClient(connection).get({
      url: '/api/v1/admin/accounts/{account_id}/users/{user_id}/settings',
      path: { account_id: accountId, user_id: userId },
    }),
  )
}

export async function updateUserMemorySettings(
  connection: AdminConnection,
  accountId: string,
  userId: string,
  memoryPolicy: UserMemoryPolicy,
): Promise<UserMemorySettings> {
  return getOvResult<UserMemorySettings>(
    createAdminClient(connection).patch({
      url: '/api/v1/admin/accounts/{account_id}/users/{user_id}/settings',
      path: { account_id: accountId, user_id: userId },
      headers: { 'Content-Type': 'application/json' },
      body: { memory_policy: memoryPolicy },
    }),
  )
}

export type AdminGroup = { group_id: string; member_count: number }

const groupsUrl = '/api/v1/admin/accounts/{account_id}/groups'

export function fetchAdminGroups(connection: AdminConnection) {
  return getOvResult<AdminGroup[]>(
    createAdminClient(connection).get({
      url: groupsUrl,
      path: { account_id: connection.accountId },
    }),
  )
}

export function createAdminGroup(connection: AdminConnection, groupId: string) {
  return getOvResult<AdminGroup>(
    createAdminClient(connection).post({
      url: groupsUrl,
      path: { account_id: connection.accountId },
      headers: { 'Content-Type': 'application/json' },
      body: { group_id: groupId },
    }),
  )
}

export function deleteAdminGroup(connection: AdminConnection, groupId: string) {
  return getOvResult<{ deleted: boolean }>(
    createAdminClient(connection).delete({
      url: `${groupsUrl}/{group_id}`,
      path: { account_id: connection.accountId, group_id: groupId },
    }),
  )
}

export function fetchAdminGroupMembers(
  connection: AdminConnection,
  groupId: string,
) {
  return getOvResult<{ group_id: string; members: string[] }>(
    createAdminClient(connection).get({
      url: `${groupsUrl}/{group_id}/members`,
      path: { account_id: connection.accountId, group_id: groupId },
    }),
  )
}

export function updateAdminGroupMember(
  connection: AdminConnection,
  groupId: string,
  userId: string,
  add: boolean,
) {
  const client = createAdminClient(connection)
  const options = {
    url: `${groupsUrl}/{group_id}/members/{user_id}`,
    path: {
      account_id: connection.accountId,
      group_id: groupId,
      user_id: userId,
    },
  }
  return getOvResult<unknown>(
    add ? client.put(options) : client.delete(options),
  )
}
