import * as React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { LoaderCircleIcon, Trash2Icon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { Input } from '#/components/ui/input'
import { useAppConnection } from '#/hooks/use-app-connection'
import { deleteAdminAccount, fetchAdminAccounts } from '#/lib/admin'
import type { AdminConnection } from '#/lib/admin'
import { PLAIN_INPUT_PROPS } from '#/lib/form-input'

import { getErrorMessage } from '../-lib/error'

export function DeleteAccountButton({
  accountId,
  adminConnection,
}: {
  accountId: string
  adminConnection: AdminConnection
}) {
  const { t } = useTranslation('settings')
  const queryClient = useQueryClient()
  const { openConnectionSettings, switchManagementAccount } = useAppConnection()
  const [open, setOpen] = React.useState(false)
  const [confirmation, setConfirmation] = React.useState('')

  const deleteAccount = useMutation({
    mutationFn: () => deleteAdminAccount(adminConnection, accountId),
    onError: (error) => toast.error(getErrorMessage(error)),
    onSuccess: async () => {
      let nextAccountId = ''
      try {
        const remainingAccounts = await fetchAdminAccounts(adminConnection)
        nextAccountId = remainingAccounts[0]?.accountId ?? ''
      } catch (error) {
        toast.warning(
          t('toast.accountDeletedRecoveryFailed', {
            error: getErrorMessage(error),
          }),
        )
      }

      await switchManagementAccount(nextAccountId)
      queryClient.removeQueries({ queryKey: ['managed-users'] })
      await queryClient.invalidateQueries({ queryKey: ['account-switcher'] })
      setOpen(false)
      setConfirmation('')
      toast.success(t('toast.accountDeleted', { account: accountId }))

      if (!nextAccountId) {
        openConnectionSettings()
      }
    },
  })

  const confirmationMatches = confirmation === accountId

  return (
    <>
      <Button
        type="button"
        variant="outline"
        disabled={!accountId}
        className="border-destructive/50 text-destructive hover:bg-destructive/10 hover:text-destructive"
        onClick={() => setOpen(true)}
      >
        <Trash2Icon />
        {t('actions.deleteAccount')}
      </Button>

      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          if (!deleteAccount.isPending) {
            setOpen(nextOpen)
            if (!nextOpen) {
              setConfirmation('')
            }
          }
        }}
      >
        <DialogContent>
          <form
            onSubmit={(event) => {
              event.preventDefault()
              if (confirmationMatches) {
                deleteAccount.mutate()
              }
            }}
          >
            <DialogHeader>
              <DialogTitle>{t('dialogs.deleteAccount.title')}</DialogTitle>
              <DialogDescription>
                {t('dialogs.deleteAccount.description', { account: accountId })}
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-2 py-5">
              <label className="grid gap-2 text-sm font-medium">
                {t('dialogs.deleteAccount.confirmLabel', {
                  account: accountId,
                })}
                <Input
                  value={confirmation}
                  disabled={deleteAccount.isPending}
                  onChange={(event) => setConfirmation(event.target.value)}
                  placeholder={accountId}
                  {...PLAIN_INPUT_PROPS}
                />
              </label>
              <p className="text-xs leading-5 text-muted-foreground">
                {t('dialogs.deleteAccount.confirmHint')}
              </p>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={deleteAccount.isPending}
                onClick={() => setOpen(false)}
              >
                {t('actions.cancel')}
              </Button>
              <Button
                type="submit"
                variant="destructive"
                disabled={!confirmationMatches || deleteAccount.isPending}
              >
                {deleteAccount.isPending ? (
                  <LoaderCircleIcon className="animate-spin" />
                ) : (
                  <Trash2Icon />
                )}
                {t('actions.confirmDeleteAccount')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}
