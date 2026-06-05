import axios from 'axios'

import { createClient } from '#/gen/ov-client/client'
import {
  getAdminAccountIdUsers,
  getAdminAccounts,
  getOvResult,
  postAdminAccountIdUserIdKey,
  postAdminAccountIdUsers,
  postAdminAccounts,
} from '#/lib/ov-client'

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
  accountId: string
  role: string
  userId: string
}

export type KeyResult = {
  accountId?: string
  apiKey: string
  userId?: string
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
  setOptionalHeader(headers, 'X-OpenViking-Account', connection.accountId)
  setOptionalHeader(headers, 'X-OpenViking-User', connection.userId)

  return createClient({
    axios: axios.create(),
    baseURL: normalizeBaseUrl(connection.baseUrl),
    headers,
    throwOnError: true,
  })
}

function normalizeAccount(value: unknown): AdminAccount | null {
  if (!isRecord(value)) {
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
      query: {
        limit: 500,
      },
    }),
  )
  return result
    .map((item) => normalizeUser(accountId, item))
    .filter((item): item is AdminUser => Boolean(item))
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

export async function createAdminUser(
  connection: AdminConnection,
  input: CreateUserInput,
): Promise<KeyResult> {
  const result = await getOvResult<unknown>(
    postAdminAccountIdUsers({
      body: {
        role: input.role,
        user_id: input.userId,
      },
      client: createAdminClient(connection),
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
