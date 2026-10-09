import i18next from 'i18next'
import { beforeAll, describe, expect, it } from 'vitest'

import en from '#/i18n/locales/en/gateway'
import zh from '#/i18n/locales/zh-CN/gateway'

import { GatewayError } from './api'
import {
  DEGRADATIONS,
  captureReasonLabel,
  degradationInfo,
  gatewayErrorMessage,
  healthReasonLabel,
  kindLabel,
  openVikingReason,
  protocolLabel,
  recallReasonLabel,
  toolSkipReasonLabel,
  windowReminderLabel,
} from './localize'
import type { Translate } from './localize'

let t: Translate
let zhT: Translate

beforeAll(async () => {
  const instance = i18next.createInstance()
  await instance.init({
    resources: {
      en: { gateway: en },
      'zh-CN': { gateway: zh },
    },
    lng: 'en',
    ns: ['gateway'],
    defaultNS: 'gateway',
    interpolation: { escapeValue: false },
  })
  t = instance.getFixedT('en', 'gateway')
  zhT = instance.getFixedT('zh-CN', 'gateway')
})

describe('enum labels', () => {
  it('translates known values and passes unknown ones through', () => {
    expect(kindLabel(t, 'user')).toBe('New message')
    expect(kindLabel(zhT, 'capture')).toBe('记忆同步')
    expect(kindLabel(t, 'brand_new_kind')).toBe('brand_new_kind')
    expect(protocolLabel(t, 'chat')).toBe('Chat Completions')
    expect(toolSkipReasonLabel(t, 'tools_require_full_history')).toContain(
      'full conversation',
    )
    expect(toolSkipReasonLabel(zhT, 'tools_unavailable')).toContain('工具清单')
    expect(windowReminderLabel(t, 'hard')).toBe(
      'Told the model to start a new window now',
    )
    expect(windowReminderLabel(zhT, 'soft')).toBe('已提醒模型尽快开启新窗口')
  })

  it('explains OpenViking reasons by pattern', () => {
    expect(openVikingReason(t, 'openviking_http_503')).toBe(
      'OpenViking returned HTTP 503',
    )
    expect(openVikingReason(t, 'openviking_http_401')).toContain('rejected')
    expect(openVikingReason(t, 'openviking_RATE_LIMITED')).toBe(
      'OpenViking reported error RATE_LIMITED',
    )
    expect(openVikingReason(t, 'history_changed')).toBeUndefined()
  })

  it('labels capture, recall and health reasons', () => {
    expect(captureReasonLabel(t, '')).toBe('')
    expect(captureReasonLabel(t, 'manual_reset')).toBe(
      'Resynced by an administrator',
    )
    expect(captureReasonLabel(t, 'openviking_unavailable')).toContain(
      'unreachable',
    )
    expect(recallReasonLabel(t, 'empty')).toBe('Nothing relevant found')
    expect(recallReasonLabel(t, undefined)).toBe('')
    expect(healthReasonLabel(t, 'openviking_version_mismatch')).toContain(
      'older',
    )
    expect(healthReasonLabel(t, 'something_else')).toBe('something_else')
  })

  it('names the provider and protocol in the unsupported-protocol message', () => {
    const values = { vendor: 'openai', protocol: 'anthropic' }
    expect(t('validation.protocolUnsupported', values)).toBe(
      'OpenAI does not offer Anthropic Messages. Pick another protocol, or set the provider to Generic.',
    )
    expect(zhT('validation.protocolUnsupported', values)).toBe(
      'OpenAI 不提供 Anthropic Messages 接口。请换一种协议，或者把服务商改成“通用”。',
    )
  })

  it('explains every degradation in both languages', () => {
    for (const reason of DEGRADATIONS) {
      for (const translate of [t, zhT]) {
        const info = degradationInfo(translate, reason)
        expect(info.label).not.toContain('enums.')
        expect(info.explanation).toBeTruthy()
        expect(info.action).toBeTruthy()
      }
    }
    expect(degradationInfo(t, 'new_reason')).toEqual({ label: 'new_reason' })
  })
})

describe('gatewayErrorMessage', () => {
  it('maps known server messages and OpenViking reasons', () => {
    expect(
      gatewayErrorMessage(
        t,
        new GatewayError(
          'Revoke or update dependent keys before deleting this object',
          409,
        ),
      ),
    ).toBe('Keys still use this. Revoke those keys first.')
    expect(
      gatewayErrorMessage(zhT, new GatewayError('root_key_not_allowed', 403)),
    ).toBe('不接受 Root 密钥，请使用本账号下的用户密钥。')
    expect(
      gatewayErrorMessage(
        t,
        new GatewayError('OpenViking key belongs to another account', 403),
      ),
    ).toBe('This OpenViking key belongs to another account.')
  })

  it('summarizes by category and keeps unexplained messages', () => {
    expect(gatewayErrorMessage(t, new GatewayError('Not Found', 404))).toBe(
      'This item no longer exists. Refresh and try again.',
    )
    expect(
      gatewayErrorMessage(
        t,
        new GatewayError('OpenViking Gateway is not enabled', 503),
      ),
    ).toBe('The OpenViking Gateway is turned off on this server.')
    expect(gatewayErrorMessage(t, new Error('socket hang up'))).toBe(
      'socket hang up',
    )
  })
})
