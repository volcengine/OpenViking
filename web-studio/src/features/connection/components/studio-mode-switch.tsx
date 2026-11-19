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
  ArrowLeftIcon,
  KeyRoundIcon,
  PanelsTopLeftIcon,
  UserRoundIcon,
  UsersRoundIcon,
} from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'

export function StudioModeSwitch({
  managementMode,
  canManage = true,
  compact = false,
  navigation = false,
}: {
  managementMode: boolean
  canManage?: boolean
  compact?: boolean
  navigation?: boolean
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
  const needsUserKey = managementMode && !hasDataIdentity && users.length === 0
  const label = needsUserKey
    ? t('accountSwitcher:scope.addUserKey')
    : t(managementMode ? 'modes.enterWorkbench' : 'modes.returnManagement')
  const content = (
    <>
      {needsUserKey ? (
        <KeyRoundIcon className="size-4" />
      ) : navigation ? (
        <ArrowLeftIcon className="size-4" />
      ) : managementMode ? (
        <PanelsTopLeftIcon className="size-4" />
      ) : (
        <UsersRoundIcon className="size-4" />
      )}
      <span
        className={navigation ? '' : compact ? 'sr-only' : 'hidden md:inline'}
      >
        {navigation && !needsUserKey ? t('modes.back') : label}
      </span>
    </>
  )
  const buttonClass = navigation
    ? 'h-8 w-full justify-start gap-2 rounded-lg border-0 bg-transparent px-2.5 text-[13px] font-normal shadow-none hover:bg-accent'
    : compact
      ? 'size-8 rounded-md border-0 bg-transparent p-0 shadow-none text-muted-foreground'
      : managementMode
        ? 'size-9 rounded-md p-0 md:w-auto md:gap-2 md:px-3'
        : 'h-7 w-7 rounded-full bg-[#5252ff] p-0 text-xs leading-5 tracking-[0.003em] text-white hover:bg-[#4545e8] md:w-auto md:gap-1 md:px-2.5'
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
  if (!managementMode || hasDataIdentity || needsUserKey) {
    return (
      <Button
        nativeButton={false}
        size="sm"
        variant={managementMode ? 'outline' : 'default'}
        aria-label={label}
        className={buttonClass}
        render={
          <Link
            search={needsUserKey ? { returnTo: '/home' } : undefined}
            to={
              needsUserKey
                ? '/connections/user-key'
                : managementMode
                  ? '/home'
                  : '/admin'
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
