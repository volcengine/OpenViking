import type { TFunction } from 'i18next'
import { describe, expect, it } from 'vitest'

import { localizeCapabilityDetail } from '#/features/connection/lib/localize-capability-probe'
import type { CapabilityDetailCode } from '#/shared/api/admin'

const translations: Record<string, string> = {
  'health.detail.accountAdminAvailable': '账号管理员权限可用',
  'health.detail.adminModeRequired': '管理 API 需要受支持的服务模式',
  'health.detail.controlKeyRequired': '需要管理密钥',
  'health.detail.dataKeyRequired': '需要用户密钥',
  'health.detail.rootAvailable': 'Root 管理权限可用',
  'health.detail.tenantDataAvailable': '租户数据访问可用',
  'health.detail.trustedIdentityRequired': '需要账号和用户信息',
  'health.detail.trustedKeyRequired':
    '需要 Root API 密钥，请配置 server.root_api_key',
}

const t = ((key: string) => translations[key] ?? key) as TFunction<'settings'>

describe('localizeCapabilityDetail', () => {
  it('maps every structured capability detail code', () => {
    const codes = Object.keys(translations).map((key) =>
      key.replace('health.detail.', ''),
    ) as CapabilityDetailCode[]

    for (const code of codes) {
      expect(
        localizeCapabilityDetail({ detailCode: code, state: 'ok' }, t),
      ).toBe(translations[`health.detail.${code}`])
    }
  })

  it('preserves an unstructured server error', () => {
    expect(
      localizeCapabilityDetail({ detail: 'HTTP 503', state: 'error' }, t),
    ).toBe('HTTP 503')
  })
})
