import { credentialStorageMode } from '#/features/connection/lib/credential-storage'
import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

export function CredentialStorageNotice() {
  const { t } = useTranslation('studio')
  useEffect(() => {
    const mode = credentialStorageMode()
    if (mode !== 'persistent') {
      toast.warning(
        t(
          mode === 'plaintext'
            ? 'credentialStorage.plaintext'
            : 'credentialStorage.sessionOnly',
        ),
        {
          id: 'credential-storage-mode',
          duration: Infinity,
        },
      )
    }
  }, [t])
  return null
}
