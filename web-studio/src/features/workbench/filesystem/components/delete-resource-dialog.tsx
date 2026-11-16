import { Loader2Icon, Trash2Icon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { deleteSharedResource } from '#/features/workbench/resources/lib/api'
import { showErrorToast } from '#/shared/lib/error-toast'
import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'
import { Input } from '#/shared/ui/input'

export function DeleteResourceDialog({
  dirty,
  isDirectory,
  onDeleted,
  onOpenChange,
  open,
  uri,
}: {
  dirty?: boolean
  isDirectory: boolean
  onDeleted: (result: { semanticStatus?: string }) => void
  onOpenChange: (open: boolean) => void
  open: boolean
  uri: string
}) {
  const { t } = useTranslation(['filesystem', 'dataManagement'])
  const [confirmation, setConfirmation] = useState('')
  const [isDeleting, setIsDeleting] = useState(false)
  const normalizedUri = uri.replace(/\/$/, '')

  useEffect(() => {
    if (open) setConfirmation('')
  }, [open, uri])

  const handleDelete = async () => {
    if (confirmation !== normalizedUri || isDeleting) return
    setIsDeleting(true)
    try {
      const result = await deleteSharedResource(normalizedUri, isDirectory)
      onOpenChange(false)
      onDeleted(result)
    } catch (error) {
      showErrorToast(error, t('deleteResource.failed'))
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!isDeleting) onOpenChange(nextOpen)
      }}
    >
      <DialogContent showCloseButton={!isDeleting}>
        <DialogHeader>
          <DialogTitle>{t('deleteResource.title')}</DialogTitle>
          <DialogDescription>
            {t(
              isDirectory
                ? 'deleteResource.directoryDescription'
                : 'deleteResource.fileDescription',
            )}
          </DialogDescription>
        </DialogHeader>
        <div className="rounded-md bg-muted p-3 font-mono text-xs break-all">
          {normalizedUri}
        </div>
        {dirty ? (
          <p className="text-sm text-destructive">
            {t('dataManagement:deleteUnsavedWarning')}
          </p>
        ) : null}
        <div className="space-y-2">
          <label htmlFor="delete-resource-confirmation" className="text-sm">
            {t('deleteResource.confirmationLabel')}
          </label>
          <Input
            id="delete-resource-confirmation"
            autoComplete="off"
            disabled={isDeleting}
            value={confirmation}
            placeholder={normalizedUri}
            onChange={(event) => setConfirmation(event.target.value)}
          />
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={isDeleting}
            onClick={() => onOpenChange(false)}
          >
            {t('deleteResource.cancel')}
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={confirmation !== normalizedUri || isDeleting}
            onClick={() => void handleDelete()}
          >
            {isDeleting ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <Trash2Icon className="size-4" />
            )}
            {t(
              isDeleting ? 'deleteResource.deleting' : 'deleteResource.confirm',
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
