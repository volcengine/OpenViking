import { afterEach, expect, it, vi } from 'vitest'
import { toast } from 'sonner'
import i18n from '#/i18n/core'
import { showErrorToast } from './error-toast'

vi.mock('sonner', () => ({ toast: { error: vi.fn() } }))
afterEach(() => {
  vi.clearAllMocks()
  return i18n.changeLanguage('en')
})

it.each(['zh-CN', 'en'])(
  'keeps raw diagnostics secondary to the %s toast summary',
  async (language) => {
    await i18n.changeLanguage(language)
    showErrorToast(
      Object.assign(new Error('Permission denied for raw/path'), {
        statusCode: 403,
      }),
    )
    expect(toast.error).toHaveBeenCalledWith(
      i18n.t('studio:errors.permissionDenied'),
      {
        description: 'Permission denied for raw/path',
      },
    )
  },
)
