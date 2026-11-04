import {
  getStudioExperience,
  isStudioConnectionAllowed,
} from '#/platform/deployment/experience'
import type { ConnectionDraft } from '#/platform/connection/types'
import i18n from '#/i18n/core'

/** Enforced before changing runtime clients or persisting an active connection. */
export function assertAllowedConnection(connection: ConnectionDraft): void {
  if (!isStudioConnectionAllowed(connection))
    throw new Error(i18n.t('onboarding:apiLogin.connectionNotAllowed'))
}

export function shouldDiscoverGatewayHost(pathname: string): boolean {
  return (
    getStudioExperience().startup.discoverGatewayHost &&
    (pathname === '/studio' || pathname.startsWith('/studio/'))
  )
}
