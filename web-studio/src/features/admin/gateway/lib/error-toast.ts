import { toast } from 'sonner'

import { toGatewayError } from '#/features/admin/gateway/lib/api'
import type { Translate } from '#/features/admin/gateway/lib/localize'
import { gatewayErrorMessage } from '#/features/admin/gateway/lib/localize'
import { requestManagementUnlock } from '#/features/admin/gateway/lib/management-key'

/**
 * Toasts why a gateway call failed. A write refused for lack of the gateway
 * management key also offers to enter it.
 */
export function toastGatewayError(t: Translate, error: unknown) {
  const message = gatewayErrorMessage(t, error)
  if (toGatewayError(error).reason === 'management_key_required') {
    toast.error(message, {
      action: {
        label: t('management.title'),
        onClick: requestManagementUnlock,
      },
    })
  } else {
    toast.error(message)
  }
}
