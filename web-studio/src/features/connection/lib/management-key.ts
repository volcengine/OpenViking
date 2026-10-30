import {
  fetchConnectionHealth,
  isUsableServerHealth,
  readServerIdentity,
} from '#/features/connection/lib/health'
import { StudioSetupRequestError } from '#/features/connection/lib/onboarding'
import type { ConnectionDraft } from '#/features/connection/lib/types'

export class ManagementKeyError extends Error {
  constructor(
    public readonly code: 'notAdmin' | 'identityMissing' | 'invalidService',
  ) {
    super(code)
  }
}

/** Validate only the submitted control key; never replace or probe the data user. */
export async function verifyManagementKey(
  connection: ConnectionDraft,
  apiKey: string,
  signal: AbortSignal,
): Promise<string> {
  const requested = {
    ...connection,
    apiKey: '',
    adminApiKey: apiKey.trim(),
    accountId: '',
    userId: '',
  }
  const health = await fetchConnectionHealth(
    requested,
    signal,
    'control',
  ).catch((cause: unknown) => {
    throw new StudioSetupRequestError('health', cause, requested)
  })
  signal.throwIfAborted()
  if (!isUsableServerHealth(health))
    throw new ManagementKeyError('invalidService')
  if (health.role !== 'root' && health.role !== 'admin')
    throw new ManagementKeyError('notAdmin')
  if (health.role === 'admin' && !readServerIdentity(health).accountId)
    throw new ManagementKeyError('identityMissing')
  return requested.adminApiKey
}
