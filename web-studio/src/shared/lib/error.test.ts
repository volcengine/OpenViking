import { afterEach, describe, expect, it } from 'vitest'
import i18n from '#/i18n/core'
import { getErrorMessage, getLocalizedErrorMessage } from './error'

afterEach(() => i18n.changeLanguage('en'))

describe('localized error summaries', () => {
  it.each(['zh-CN', 'en'])(
    'translates known errors in %s without replacing diagnostics',
    async (language) => {
      await i18n.changeLanguage(language)
      for (const [statusCode, key] of [
        [400, 'requestInvalid'],
        [422, 'requestInvalid'],
        [401, 'authenticationFailed'],
        [403, 'permissionDenied'],
        [404, 'resourceNotFound'],
        [409, 'requestConflict'],
        [413, 'payloadTooLarge'],
        [429, 'rateLimited'],
        [500, 'serviceUnavailable'],
        [503, 'serviceUnavailable'],
        [408, 'requestTimedOut'],
        [504, 'requestTimedOut'],
      ] as const) {
        const error = Object.assign(new Error('Raw server diagnostic'), {
          statusCode,
        })
        expect(getLocalizedErrorMessage(error)).toBe(
          i18n.t(`studio:errors.${key}`),
        )
        expect(getErrorMessage(error)).toBe('Raw server diagnostic')
      }
      expect(getLocalizedErrorMessage({ response: { status: 403 } })).toBe(
        i18n.t('studio:errors.permissionDenied'),
      )
      expect(getLocalizedErrorMessage({ code: 'UNAUTHENTICATED' })).toBe(
        i18n.t('studio:errors.authenticationFailed'),
      )
      expect(getLocalizedErrorMessage({ code: 'NETWORK_ERROR' })).toBe(
        i18n.t('studio:errors.networkFailed'),
      )
      expect(getLocalizedErrorMessage({ code: 'ETIMEDOUT' })).toBe(
        i18n.t('studio:errors.requestTimedOut'),
      )
      expect(getLocalizedErrorMessage(new Error('Unknown diagnostic'))).toBe(
        i18n.t('studio:errors.requestFailed'),
      )
      expect(
        getLocalizedErrorMessage(undefined, 'Localized operation context'),
      ).toBe('Localized operation context')
    },
  )
})
