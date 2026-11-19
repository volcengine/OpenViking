import { useEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  createOvClient,
  deleteFs,
  getOvClient,
  getOvResult,
  normalizeOvClientError,
} from '#/shared/api/ov-client'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '#/shared/ui/alert-dialog'

export function DeleteDocumentDialog({
  uri,
  name,
  dirty,
  onClose,
  onDeleted,
}: {
  uri: string
  name: string
  dirty: boolean
  onClose: () => void
  onDeleted: (uri: string) => void
}) {
  const { t } = useTranslation('dataManagement')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const submitting = useRef(false)
  const controller = useRef<AbortController | null>(null)
  const source = useMemo(() => {
    const adapter = getOvClient()
    return createOvClient({
      ...adapter.getOptions(),
      connection: adapter.getConnection(),
      credentialScope: 'data',
      apiKeyStorageKey: '',
      bindSdkClient: false,
    })
  }, [])
  // Scope the network operation to this confirmation. Closing, changing identity
  // or unmounting cancels the request and suppresses late UI updates.
  useEffect(() => {
    const active = new AbortController()
    controller.current = active
    return () => active.abort()
  }, [])

  const remove = async () => {
    const signal = controller.current?.signal
    if (!signal || submitting.current) return
    const isActive = () => !signal.aborted
    if (!isActive()) return
    submitting.current = true
    setPending(true)
    setError('')
    try {
      await getOvResult(
        deleteFs({
          client: source.client,
          query: { uri, recursive: false },
          signal,
        }),
      )
    } catch (cause) {
      if (!isActive()) return
      setError(
        t('deleteFailed', { error: normalizeOvClientError(cause).message }),
      )
      submitting.current = false
      setPending(false)
      return
    }
    if (isActive()) onDeleted(uri)
  }
  return (
    <AlertDialog
      open
      onOpenChange={(open) => {
        if (!open && !submitting.current) onClose()
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t('deleteConfirmTitle')}</AlertDialogTitle>
          <AlertDialogDescription>
            {t('deleteConfirmDescription', { name })}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <p className="break-all text-xs text-muted-foreground">{uri}</p>
        {dirty ? (
          <p className="text-sm text-destructive">
            {t('deleteUnsavedWarning')}
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>
            {t('deleteCancel')}
          </AlertDialogCancel>
          <AlertDialogAction
            disabled={pending}
            className="bg-destructive text-white hover:bg-destructive/90"
            onClick={(event) => {
              event.preventDefault()
              void remove()
            }}
          >
            {t(pending ? 'deletingDocument' : 'deleteConfirm')}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
