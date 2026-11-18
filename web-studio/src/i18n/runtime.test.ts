// @vitest-environment jsdom
import {
  OvClientError,
  normalizeOvClientError,
} from '#/shared/api/ov-client/errors'
import { afterEach, describe, expect, it } from 'vitest'
import { formatDateTime, formatNumber } from './format'
import i18n from './index'

afterEach(async () => {
  await i18n.changeLanguage('en')
})

describe('Studio language at runtime', () => {
  it('synchronizes document language on every switch', async () => {
    for (const language of ['zh-CN', 'en']) {
      await i18n.changeLanguage(language)
      expect(document.documentElement.lang).toBe(language)
    }
  })

  it('formats dates and numbers with the UI language instead of the browser default', async () => {
    const date = new Date('2026-10-19T08:30:00Z')
    const options = { month: 'short', day: 'numeric', timeZone: 'UTC' } as const
    for (const language of ['zh-CN', 'en']) {
      await i18n.changeLanguage(language)
      expect(formatDateTime(date, options)).toBe(
        new Intl.DateTimeFormat(language, options).format(date),
      )
      expect(formatNumber(12345)).toBe(
        new Intl.NumberFormat(language).format(12345),
      )
    }
  })

  it('localizes API-key guidance and keeps server diagnostics intact', async () => {
    await i18n.changeLanguage('zh-CN')
    const error = new OvClientError({
      code: 'UNAUTHENTICATED',
      message: 'Missing API key',
      details: { trace: 'raw' },
    })
    expect(error.message).toBe('Missing API key 请前往设置配置 X-API-Key。')
    expect(error.details).toEqual({ trace: 'raw' })
    expect(normalizeOvClientError(error)).toBe(error)
    await i18n.changeLanguage('en')
    expect(
      new OvClientError({ code: 'UNAUTHENTICATED', message: 'Missing API key' })
        .message,
    ).toBe('Missing API key Please go to Settings and set X-API-Key.')
  })
})
