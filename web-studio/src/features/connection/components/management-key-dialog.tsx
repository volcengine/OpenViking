import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { flushCredentialStorage } from '#/features/connection/lib/credential-storage'
import {
  ManagementKeyError,
  verifyManagementKey,
} from '#/features/connection/lib/management-key'
import { ErrorMessage } from '#/shared/components/error-message'
import { PLAIN_INPUT_PROPS } from '#/shared/lib/form-input'
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
import { Label } from '#/shared/ui/label'
import { useNavigate } from '@tanstack/react-router'
import { LayoutDashboardIcon, LoaderCircleIcon } from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

export function ManagementKeyDialog({ className }: { className?: string }) {
  const { t } = useTranslation(['studio', 'appShell'])
  const { connection, saveConnection } = useAppConnection()
  const navigate = useNavigate()
  const [open, setOpen] = React.useState(false)
  const [key, setKey] = React.useState('')
  const [error, setError] = React.useState<Error | null>(null)
  const [pending, setPending] = React.useState(false)
  const request = React.useRef<AbortController | null>(null)
  React.useEffect(() => () => request.current?.abort(), [])

  function changeOpen(next: boolean) {
    if (!next) request.current?.abort()
    setOpen(next)
    setKey('')
    setError(null)
    setPending(false)
  }

  async function submit(event: React.SubmitEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending || !key.trim()) return
    const controller = new AbortController()
    request.current = controller
    setPending(true)
    setError(null)
    let verified: string
    try {
      verified = await verifyManagementKey(connection, key, controller.signal)
      if (controller.signal.aborted) return
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(
          cause instanceof Error ? cause : new Error(t('errors.requestFailed')),
        )
        setPending(false)
      }
      return
    }
    // Saving remounts the shell. Finish encrypted persistence and entry after
    // that intentional unmount, keeping the accepted data identity unchanged.
    request.current = null
    try {
      saveConnection({ ...connection, adminApiKey: verified })
    } catch {
      toast.error(t('credentialStorage.saveError'))
      setPending(false)
      return
    }
    try {
      await flushCredentialStorage()
    } catch {
      toast.error(t('credentialStorage.saveError'))
    }
    await navigate({ to: '/admin' })
  }

  return (
    <>
      <Button
        size="sm"
        variant="outline"
        className={className}
        aria-label={t('appShell:modes.returnManagement')}
        onClick={() => changeOpen(true)}
      >
        <LayoutDashboardIcon className="size-4" />
        <span className="hidden md:inline">
          {t('appShell:modes.returnManagement')}
        </span>
      </Button>
      <Dialog open={open} onOpenChange={changeOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('managementKey.title')}</DialogTitle>
            <DialogDescription>
              {t('managementKey.description')}
            </DialogDescription>
          </DialogHeader>
          <form className="grid gap-4" onSubmit={submit}>
            <p className="break-all rounded-lg bg-muted px-3 py-2 text-sm text-muted-foreground">
              {connection.baseUrl}
            </p>
            <div className="grid gap-2">
              <Label htmlFor="management-api-key">
                {t('managementKey.label')}
              </Label>
              <Input
                id="management-api-key"
                type="password"
                value={key}
                disabled={pending}
                onChange={(event) => {
                  setKey(event.target.value)
                  setError(null)
                }}
                {...PLAIN_INPUT_PROPS}
              />
              <p className="text-sm text-muted-foreground">
                {t('managementKey.scopeHint')}
              </p>
            </div>
            {error ? (
              <ErrorMessage
                error={
                  error instanceof ManagementKeyError
                    ? t(`managementKey.${error.code}`)
                    : error
                }
              />
            ) : null}
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => changeOpen(false)}
              >
                {t('profiles.cancel')}
              </Button>
              <Button type="submit" disabled={pending || !key.trim()}>
                {pending ? (
                  <LoaderCircleIcon className="size-4 animate-spin" />
                ) : null}
                {t(
                  pending ? 'managementKey.verifying' : 'managementKey.submit',
                )}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}
