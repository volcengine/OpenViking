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
import { useDeleteSession } from '#/lib/sessions/use-sessions'

export function DeleteConversation({
  id,
  title,
  onDeleted,
}: {
  id: string
  title: string
  onDeleted: () => void
}) {
  const { t } = useTranslation('sessions')
  const [open, setOpen] = useState(false)
  const mutation = useDeleteSession()
  return (
    <>
      <Button
        size="icon"
        variant="ghost"
        className="mr-1 shrink-0 text-muted-foreground hover:text-destructive [@media(hover:hover)]:opacity-0 group-hover/conversation:opacity-100 group-focus-within/conversation:opacity-100 focus-visible:opacity-100 transition-opacity"
        aria-label={t('threadList.deleteSession', { title })}
        onClick={() => {
          mutation.reset()
          setOpen(true)
        }}
      >
        <Trash2Icon className="size-4" />
      </Button>
      <AlertDialog
        open={open}
        onOpenChange={(value) => {
          if (!mutation.isPending) setOpen(value)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('threadList.deleteConfirmTitle')}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('threadList.deleteConfirmDescription', { title })}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {mutation.error && (
            <p role="alert" className="text-sm text-destructive">
              {t('threadList.deleteFailed', { error: mutation.error.message })}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={mutation.isPending}>
              {t('threadList.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-white hover:bg-destructive/90"
              disabled={mutation.isPending}
              onClick={async (event) => {
                event.preventDefault()
                if (mutation.isPending) return
                try {
                  await mutation.mutateAsync(id)
                  setOpen(false)
                  onDeleted()
                } catch {
                  // Keep the dialog and error visible so the user can retry.
                }
              }}
            >
              {t(
                mutation.isPending
                  ? 'threadList.deleting'
                  : 'threadList.confirmDelete',
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
