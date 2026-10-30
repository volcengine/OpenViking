// @vitest-environment jsdom
import { act, cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import i18n from '#/i18n'
import { StudioSetupRequestError } from '#/features/connection/lib/onboarding'
import { OvClientError } from '#/shared/api/ov-client/errors'
import { ErrorMessage } from './error-message'

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('en')
})

it('translates the main error on language changes and keeps redacted details collapsed', async () => {
  await i18n.changeLanguage('zh-CN')
  const error = new StudioSetupRequestError(
    'data',
    new OvClientError({
      message: 'Invalid secret-user-key secret-root-key',
      statusCode: 401,
      code: 'UNAUTHENTICATED',
    }),
    {
      baseUrl: 'http://service.test',
      serviceSelection: 'opensource',
      accountId: 'default',
      userId: 'alice',
      apiKey: 'secret-user-key',
      adminApiKey: 'secret-root-key',
    },
  )
  // The request wrapper is responsible for credential redaction, independently of presentation.
  render(<ErrorMessage error={error} />)
  const alert = screen.getByRole('alert')
  expect(alert.querySelector('p')?.textContent).toBe(
    i18n.t('studio:errors.authenticationFailed'),
  )
  expect(alert.querySelector('details')?.open).toBe(false)
  expect(within(alert).getByText('技术详情')).toBeTruthy()
  expect(alert.textContent).not.toContain('secret-user-key')
  expect(alert.textContent).not.toContain('secret-root-key')
  expect(alert.textContent).toContain('[redacted]')
  await act(() => i18n.changeLanguage('en'))
  expect(alert.querySelector('p')?.textContent).toBe(
    i18n.t('studio:errors.authenticationFailed'),
  )
  expect(within(alert).getByText('Technical details')).toBeTruthy()
})

it('renders already translated validation messages directly', () => {
  render(<ErrorMessage error="请填写服务地址" />)
  expect(screen.getByRole('alert').textContent).toBe('请填写服务地址')
  expect(screen.queryByText('Technical details')).toBeNull()
})
