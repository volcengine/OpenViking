import * as React from 'react'
import { LoaderCircleIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '#/components/ui/alert-dialog'

type ConfirmDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** A question, e.g. "Revoke “Laptop”?". */
  title: string
  /** What happens and what is kept. */
  description: string
  confirmLabel: string
  /** Icon of the confirm button while idle. */
  icon?: React.ReactNode
  /** Soft-red confirm button; on by default. */
  destructive?: boolean
  /**
   * Runs on confirm. Return a promise (e.g. `mutateAsync`) to show a spinner
   * and close on success; a rejection keeps the dialog open.
   */
  onConfirm: () => Promise<unknown> | void
}

/**
 * Controlled confirmation for destructive actions. Callers keep the target
 * in state and pass `open={target !== null}`; the dialog keeps showing the
 * last title and description while it animates closed after they clear it.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  icon,
  destructive = true,
  onConfirm,
}: ConfirmDialogProps) {
  const { t } = useTranslation('contextGateway')
  const [pending, setPending] = React.useState(false)
  const [shown, setShown] = React.useState({ title, description })
  if (open && (shown.title !== title || shown.description !== description)) {
    setShown({ title, description })
  }

  async function confirm() {
    setPending(true)
    try {
      await onConfirm()
      onOpenChange(false)
    } catch {
      // The caller reports the error; keep the dialog open to retry.
    } finally {
      setPending(false)
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={(next) => {
        if (!pending) onOpenChange(next)
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{open ? title : shown.title}</AlertDialogTitle>
          <AlertDialogDescription>
            {open ? description : shown.description}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>
            {t('actions.cancel')}
          </AlertDialogCancel>
          <AlertDialogAction
            variant={destructive ? 'destructive' : 'default'}
            disabled={pending}
            onClick={() => void confirm()}
          >
            {pending ? <LoaderCircleIcon className="animate-spin" /> : icon}
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
