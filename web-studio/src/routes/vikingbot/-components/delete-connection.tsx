import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Trash2Icon } from 'lucide-react'
import { Button } from '#/components/ui/button'
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
import { useMutation } from '@tanstack/react-query'
import { deleteConnection } from '../-api'
import type { Connection } from '../-api'

export function DeleteConnection({
  connection,
  title,
  onDeleted,
}: {
  connection: Connection
  title: string
  onDeleted: () => void
}) {
  const { t } = useTranslation('vikingbot')
  const [open, setOpen] = useState(false)
  const mutation = useMutation({
    mutationFn: () => deleteConnection(connection),
  })
  return (
    <>
      <Button
        variant="ghost"
        className="mr-1 shrink-0 text-muted-foreground hover:text-destructive"
        aria-label={t('deleteConnection', { title })}
        onClick={() => {
          mutation.reset()
          setOpen(true)
        }}
      >
        <Trash2Icon className="size-4" />
        {t('deleteConnection')}
      </Button>
      <AlertDialog
        open={open}
        onOpenChange={(value) => {
          if (!mutation.isPending) setOpen(value)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('deleteConnection')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('deleteConnectionHint', { title })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {mutation.error && (
            <p role="alert" className="text-sm text-destructive">
              {t('deleteFailed', { error: mutation.error.message })}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={mutation.isPending}>
              {t('cancelDelete')}
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              disabled={mutation.isPending}
              onClick={async (event) => {
                event.preventDefault()
                if (mutation.isPending) return
                try {
                  await mutation.mutateAsync()
                  setOpen(false)
                  onDeleted()
                } catch {
                  // Keep the dialog and error visible so the user can retry.
                }
              }}
            >
              {t(
                mutation.isPending
                  ? 'deletingConnection'
                  : 'confirmDeleteConnection',
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
