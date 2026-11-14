import { credentialStorageMode } from '#/features/connection/lib/credential-storage'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

export function CredentialStorageNotice() {
  const { t } = useTranslation('studio')
  useEffect(() => {
    if (credentialStorageMode() === 'session') {
      toast.warning(t('credentialStorage.sessionOnly'), {
        id: 'credential-storage-mode',
        duration: Infinity,
      })
    }
  }, [t])
  return null
}

type StorageErrorKey =
  | 'credentialStorage.conflictError'
  | 'credentialStorage.saveError'

export function registerCredentialStorageNotifications(
  translate: (key: StorageErrorKey) => string,
) {
  const onError = (event: Event) => {
    const key =
      event instanceof CustomEvent && event.detail === 'conflict'
        ? 'credentialStorage.conflictError'
        : 'credentialStorage.saveError'
    toast.error(translate(key), {
      id: 'credential-storage',
      duration: Infinity,
    })
  }
  const onRecovery = () => {
    toast.dismiss('credential-storage')
  }
  window.addEventListener('ov-credential-storage-error', onError)
  window.addEventListener('ov-credential-storage-saved', onRecovery)
  return () => {
    window.removeEventListener('ov-credential-storage-error', onError)
    window.removeEventListener('ov-credential-storage-saved', onRecovery)
  }
}
