import i18n from '#/i18n/core'
import { OvClientError } from './errors'
import type { OvConnectionState } from './types'

export function adminDelegationHeaders(
  connection: Readonly<OvConnectionState>,
): Record<string, string> {
  if (!connection.adminDelegation) return {}
  if (!connection.delegationVerified) {
    throw new OvClientError({
      code: 'PERMISSION_DENIED',
      statusCode: 403,
      message: i18n.t('studio:delegation.unverified'),
    })
  }
  return {
    Authorization: `OpenViking-Delegate ${connection.adminApiKey}`,
    'X-OpenViking-Delegate-User': connection.userId,
    'X-OpenViking-Account': connection.accountId,
  }
}
