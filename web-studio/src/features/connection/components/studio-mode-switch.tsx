import { showErrorToast } from '#/shared/lib/error-toast'
import { ManagementKeyDialog } from './management-key-dialog'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { flushCredentialStorage } from '#/features/connection/lib/credential-storage'
import type { SavedDataIdentity } from '#/features/connection/lib/profiles'
import { savedDataIdentities } from '#/features/connection/lib/profiles'
import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'
import { Link, useNavigate } from '@tanstack/react-router'
import {
  LayoutDashboardIcon,
  PanelsTopLeftIcon,
  UserRoundIcon,
} from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'

export function StudioModeSwitch({
  managementMode,
  canManage = true,
}: {
  managementMode: boolean
  canManage?: boolean
}) {
  const { t } = useTranslation('appShell')
  const { connection, profiles, activeProfileId, serverMode, switchIdentity } =
    useAppConnection()
  const navigate = useNavigate()
  const [open, setOpen] = React.useState(false)
  const [pending, setPending] = React.useState(false)
  const request = React.useRef<AbortController | null>(null)
  React.useEffect(() => () => request.current?.abort(), [])
  const previousUser = profiles.find(
    (profile) => profile.id === activeProfileId,
  )?.workbenchIdentity
  const hasDataIdentity =
    Boolean(connection.apiKey || previousUser?.apiKey) ||
    serverMode === 'trusted' ||
    serverMode === 'dev'
  const users = savedDataIdentities(profiles, connection)
    .filter((identity) => identity.accountId === connection.accountId)
    .filter(
      (identity, index, identities) =>
        identities.findIndex((other) => other.userId === identity.userId) ===
        index,
    )
  const label = t(
    managementMode ? 'modes.enterWorkbench' : 'modes.returnManagement',
  )
  const content = (
    <>
      {managementMode ? (
        <PanelsTopLeftIcon className="size-4" />
      ) : (
        <LayoutDashboardIcon className="size-4" />
      )}
      <span className="hidden md:inline">{label}</span>
    </>
  )
  const buttonClass = 'size-9 rounded-md p-0 md:w-auto md:gap-2 md:px-3'
  async function enter(identity: SavedDataIdentity) {
    if (pending) return
    const controller = new AbortController()
    request.current = controller
    setPending(true)
    try {
      await switchIdentity({
        accountId: identity.accountId,
        userId: identity.userId,
        apiKey: identity.apiKey,
        preserveProfile: true,
        allowLegacyIdentityFallback: true,
        signal: controller.signal,
      })
      // Successful scope changes remount this control. Persist the accepted
      // switch before entering the workbench; cancellation is checked by the hook.
      await flushCredentialStorage()
      await navigate({ to: '/home' })
    } catch (error) {
      if (!controller.signal.aborted) showErrorToast(error)
    } finally {
      setPending(false)
    }
  }
  if (!managementMode && !canManage)
    return <ManagementKeyDialog className={buttonClass} />
  if (!managementMode || hasDataIdentity || users.length === 0) {
    return (
      <Button
        nativeButton={false}
        size="sm"
        variant="outline"
        aria-label={label}
        className={buttonClass}
        render={
          <Link
            search={
              managementMode && !hasDataIdentity
                ? { returnTo: '/home' }
                : undefined
            }
            to={
              !managementMode
                ? '/admin'
                : hasDataIdentity
                  ? '/home'
                  : '/connections/user-key'
            }
          />
        }
      >
        {content}
      </Button>
    )
  }
  return (
    <>
      <Button
        size="sm"
        variant="outline"
        aria-label={label}
        className={buttonClass}
        disabled={pending}
        onClick={() => {
          if (users.length === 1) void enter(users[0])
          else setOpen(true)
        }}
      >
        {content}
      </Button>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) request.current?.abort()
          setOpen(next)
        }}
      >
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t('modes.selectUser')}</DialogTitle>
            <DialogDescription>
              {t('modes.selectUserDescription')}
            </DialogDescription>
          </DialogHeader>
          <div className="flex max-h-64 flex-col gap-1 overflow-y-auto">
            {users.map((identity) => (
              <Button
                key={identity.userId}
                variant="ghost"
                className="justify-start"
                disabled={pending}
                onClick={() => void enter(identity)}
              >
                <UserRoundIcon className="size-4" />
                {identity.userId}
              </Button>
            ))}
          </div>
          <Link
            to="/connections/user-key"
            search={{ returnTo: '/home' }}
            onClick={() => request.current?.abort()}
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            {t('accountSwitcher:scope.addUserKey')}
          </Link>
        </DialogContent>
      </Dialog>
    </>
  )
}
