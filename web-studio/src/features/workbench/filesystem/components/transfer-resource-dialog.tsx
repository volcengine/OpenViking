import { CopyIcon, Loader2Icon, PencilIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { transferSharedResource } from '#/features/workbench/resources/lib/api'
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

export type ResourceTransferOperation = 'copy' | 'move'

function isValidTarget(source: string, target: string): boolean {
  const normalizedTarget = target.replace(/\/$/, '')
  if (
    !normalizedTarget.startsWith('viking://resources/') ||
    normalizedTarget === source
  ) {
    return false
  }
  try {
    return !decodeURIComponent(normalizedTarget)
      .replace(/\\/g, '/')
      .split('/')
      .some((segment) => segment === '.' || segment === '..')
  } catch {
    return false
  }
}

export function TransferResourceDialog({
  isDirectory,
  onCompleted,
  onOpenChange,
  open,
  operation,
  uri,
}: {
  isDirectory: boolean
  onCompleted: (targetUri: string) => void
  onOpenChange: (open: boolean) => void
  open: boolean
  operation: ResourceTransferOperation
  uri: string
}) {
  const { t } = useTranslation('filesystem')
  const sourceUri = uri.replace(/\/$/, '')
  const [targetUri, setTargetUri] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const isCopy = operation === 'copy'

  useEffect(() => {
    if (open) setTargetUri(isCopy ? `${sourceUri}-copy` : sourceUri)
  }, [isCopy, open, sourceUri])

  const handleSubmit = async () => {
    if (!isValidTarget(sourceUri, targetUri) || isSubmitting) return
    setIsSubmitting(true)
    const normalizedTarget = targetUri.replace(/\/$/, '')
    try {
      await transferSharedResource(
        operation,
        sourceUri,
        normalizedTarget,
        isDirectory,
      )
      onOpenChange(false)
      onCompleted(normalizedTarget)
    } catch (error) {
      showErrorToast(
        error,
        t(
          isCopy
            ? 'transferResource.copyFailed'
            : 'transferResource.moveFailed',
        ),
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!isSubmitting) onOpenChange(nextOpen)
      }}
    >
      <DialogContent showCloseButton={!isSubmitting}>
        <DialogHeader>
          <DialogTitle>
            {t(
              isCopy
                ? 'transferResource.copyTitle'
                : 'transferResource.moveTitle',
            )}
          </DialogTitle>
          <DialogDescription>
            {t(
              isCopy
                ? 'transferResource.copyDescription'
                : 'transferResource.moveDescription',
            )}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <span className="text-sm text-muted-foreground">
            {t('transferResource.sourceLabel')}
          </span>
          <div className="rounded-md bg-muted p-3 font-mono text-xs break-all">
            {sourceUri}
          </div>
        </div>
        <div className="space-y-2">
          <label htmlFor={`${operation}-resource-target`} className="text-sm">
            {t('transferResource.targetLabel')}
          </label>
          <Input
            id={`${operation}-resource-target`}
            autoComplete="off"
            disabled={isSubmitting}
            value={targetUri}
            placeholder={t('transferResource.targetPlaceholder')}
            onChange={(event) => setTargetUri(event.target.value)}
          />
          <p className="text-xs text-muted-foreground">
            {t('transferResource.targetHint')}
          </p>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            disabled={isSubmitting}
            onClick={() => onOpenChange(false)}
          >
            {t('transferResource.cancel')}
          </Button>
          <Button
            type="button"
            disabled={!isValidTarget(sourceUri, targetUri) || isSubmitting}
            onClick={() => void handleSubmit()}
          >
            {isSubmitting ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : isCopy ? (
              <CopyIcon className="size-4" />
            ) : (
              <PencilIcon className="size-4" />
            )}
            {t(
              isSubmitting
                ? 'transferResource.submitting'
                : isCopy
                  ? 'transferResource.copyConfirm'
                  : 'transferResource.moveConfirm',
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
