import { showErrorToast } from '#/shared/lib/error-toast'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import type { AdminConnection } from '#/shared/api/admin'
import { createAdminAccount } from '#/shared/api/admin'
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
import { PlusIcon } from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

export function ScopeCreateAccount({
  connection,
  onCreated,
}: {
  connection: AdminConnection
  onCreated: () => void
}) {
  const { t } = useTranslation('accountSwitcher')
  const { identityScopeKey, setGeneratedCredential, switchManagementAccount } =
    useAppConnection()
  const [open, setOpen] = React.useState(false)
  const [accountId, setAccountId] = React.useState('')
  const [adminUserId, setAdminUserId] = React.useState('default')
  const [pending, setPending] = React.useState(false)
  const active = React.useRef(true)
  const scope = React.useRef(identityScopeKey)
  scope.current = identityScopeKey
  React.useEffect(() => {
    active.current = true
    return () => {
      active.current = false
    }
  }, [])
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (pending || !accountId.trim() || !adminUserId.trim()) return
    const source = identityScopeKey
    setPending(true)
    try {
      const result = await createAdminAccount(connection, {
        accountId: accountId.trim(),
        adminUserId: adminUserId.trim(),
      })
      if (!active.current || scope.current !== source) return
      if (result.apiKey)
        setGeneratedCredential({
          accountId: result.accountId || accountId.trim(),
          userId: result.userId || adminUserId.trim(),
          apiKey: result.apiKey,
        })
      setOpen(false)
      onCreated()
      await switchManagementAccount(result.accountId || accountId.trim(), {
        preserveProfile: true,
      })
      toast.success(
        t('toast.managementSwitched', {
          account: result.accountId || accountId.trim(),
        }),
      )
    } catch (error) {
      if (active.current && scope.current === source) showErrorToast(error)
    } finally {
      if (active.current) setPending(false)
    }
  }
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        className="mt-1 w-full justify-start border-t"
        onClick={() => setOpen(true)}
      >
        <PlusIcon className="size-3.5" />
        {t('create')}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!pending) setOpen(next)
        }}
      >
        <DialogContent>
          <form
            onSubmit={(event) => {
              void submit(event)
            }}
          >
            <DialogHeader>
              <DialogTitle>{t('dialog.title')}</DialogTitle>
              <DialogDescription>
                {t('scope.createDescription')}
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-5">
              <label className="grid gap-2 text-sm">
                {t('dialog.accountLabel')}
                <Input
                  value={accountId}
                  onChange={(event) => setAccountId(event.target.value)}
                  disabled={pending}
                  required
                  autoComplete="off"
                />
              </label>
              <label className="grid gap-2 text-sm">
                {t('dialog.adminLabel')}
                <Input
                  value={adminUserId}
                  onChange={(event) => setAdminUserId(event.target.value)}
                  disabled={pending}
                  required
                  autoComplete="off"
                />
              </label>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                disabled={pending}
                onClick={() => setOpen(false)}
              >
                {t('dialog.cancel')}
              </Button>
              <Button
                type="submit"
                disabled={pending || !accountId.trim() || !adminUserId.trim()}
              >
                {t('dialog.submit')}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}
