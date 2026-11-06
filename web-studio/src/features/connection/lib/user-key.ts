import {
  fetchConnectionHealth,
  isUsableServerHealth,
  readServerIdentity,
} from '#/features/connection/lib/health'
import {
  StudioSetupRequestError,
  verifyStudioSetup,
} from '#/features/connection/lib/onboarding'
import type { ConnectionDraft } from '#/features/connection/lib/types'

export function validateUserKeySearch(search: Record<string, unknown>): {
  returnTo?: '/home'
} {
  return search.returnTo === '/home' ? { returnTo: '/home' } : {}
}

export class UserKeyError extends Error {
  constructor(
    public readonly code: 'rootKey' | 'accountMismatch' | 'identityMissing',
  ) {
    super(code)
  }
}

/** Resolve the key itself, without Root credentials or asserted tenant headers. */
export async function verifyUserKey(
  current: ConnectionDraft,
  apiKey: string,
  signal: AbortSignal,
): Promise<ConnectionDraft> {
  const requested = {
    ...current,
    adminApiKey: '',
    apiKey: apiKey.trim(),
    accountId: '',
    userId: '',
  }
  const health = await fetchConnectionHealth(requested, signal).catch(
    (cause: unknown) => {
      throw new StudioSetupRequestError('health', cause, requested)
    },
  )
  signal.throwIfAborted()
  if (!isUsableServerHealth(health)) throw new UserKeyError('identityMissing')
  if (health.role === 'root') throw new UserKeyError('rootKey')
  const identity = readServerIdentity(health)
  if (!identity.accountId || !identity.userId)
    throw new UserKeyError('identityMissing')
  if (identity.accountId !== current.accountId)
    throw new UserKeyError('accountMismatch')
  const verified = await verifyStudioSetup(
    { ...requested, ...identity },
    signal,
  )
  if (!verified.apiKey) throw new UserKeyError('rootKey')
  if (verified.accountId !== current.accountId)
    throw new UserKeyError('accountMismatch')
  return { ...verified, adminApiKey: current.adminApiKey }
}
