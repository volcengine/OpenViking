import { toast } from 'sonner'
import { getErrorMessage, getLocalizedErrorMessage } from './error'

export function showErrorToast(error: unknown, fallback?: string) {
  const message = getLocalizedErrorMessage(error, fallback)
  const diagnostic = getErrorMessage(error)
  toast.error(message, {
    description: diagnostic !== message ? diagnostic : undefined,
  })
}
