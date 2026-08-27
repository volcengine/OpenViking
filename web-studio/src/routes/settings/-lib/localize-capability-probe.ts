import type { TFunction } from 'i18next'

import type { CapabilityDetailCode, CapabilityProbeResult } from '#/lib/admin'

type SettingsTranslator = TFunction<'settings'>

const detailKeys = {
  accountAdminAvailable: 'health.detail.accountAdminAvailable',
  adminModeRequired: 'health.detail.adminModeRequired',
  controlKeyRequired: 'health.detail.controlKeyRequired',
  dataKeyRequired: 'health.detail.dataKeyRequired',
  rootAvailable: 'health.detail.rootAvailable',
  tenantDataAvailable: 'health.detail.tenantDataAvailable',
  trustedIdentityRequired: 'health.detail.trustedIdentityRequired',
} as const satisfies Record<CapabilityDetailCode, string>

export function localizeCapabilityDetail(
  result: CapabilityProbeResult | undefined,
  t: SettingsTranslator,
): string | undefined {
  if (result?.detailCode) {
    return t(detailKeys[result.detailCode])
  }
  return result?.detail
}
