import { toast } from 'sonner'

import { toGatewayError } from './api'
import { gatewayErrorMessage } from './localize'
import type { Translate } from './localize'
import { requestManagementUnlock } from './management-key'

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
