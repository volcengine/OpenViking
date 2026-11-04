import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { fetchAdminAccounts } from '#/shared/api/admin'
import { showErrorToast } from '#/shared/lib/error-toast'
import { Button } from '#/shared/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '#/shared/ui/popover'
import { useQuery } from '@tanstack/react-query'
import {
  BoxIcon,
  CheckIcon,
  ChevronDownIcon,
  LoaderCircleIcon,
} from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export function UserSpaceSwitcher({
  canManageAccounts,
}: {
  canManageAccounts: boolean
}) {
  const { t } = useTranslation('appShell')
  const { connection, identityScopeKey, switchManagementAccount } =
    useAppConnection()
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState(false)
  const accounts = useQuery({
    queryKey: ['data-spaces', identityScopeKey],
    queryFn: ({ signal }) =>
      fetchAdminAccounts(
        { ...connection, apiKey: connection.adminApiKey },
        signal,
      ),
    enabled: canManageAccounts && open,
    retry: false,
  })
  async function choose(accountId: string) {
    setPending(true)
    try {
      await switchManagementAccount(accountId, { preserveProfile: true })
      setOpen(false)
    } catch (error) {
      showErrorToast(error)
    } finally {
      setPending(false)
    }
  }
  const label = (
    <>
      <BoxIcon className="size-4 shrink-0" />
      <span className="max-w-48 truncate">{connection.accountId}</span>
    </>
  )
  if (!canManageAccounts)
    return <div className="users-space-trigger">{label}</div>
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className="users-space-trigger"
        aria-label={t('adminNavigation.currentSpace', {
          space: connection.accountId,
        })}
      >
        {label}
        <ChevronDownIcon className="ml-auto size-3 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent align="start" className="w-64 gap-1 p-1">
        {accounts.isPending ? (
          <p role="status" className="p-3 text-sm text-muted-foreground">
            {t('spaces.loading')}
          </p>
        ) : accounts.isError ? (
          <Button variant="ghost" onClick={() => void accounts.refetch()}>
            {t('spaces.retry')}
          </Button>
        ) : (
          accounts.data.map((account) => (
            <Button
              key={account.accountId}
              variant="ghost"
              className="justify-start"
              disabled={pending}
              onClick={() => void choose(account.accountId)}
            >
              <BoxIcon className="size-4" />
              <span className="flex-1 truncate text-left">
                {account.accountId}
              </span>
              {pending ? (
                <LoaderCircleIcon className="size-3 animate-spin" />
              ) : account.accountId === connection.accountId ? (
                <CheckIcon className="size-3" />
              ) : null}
            </Button>
          ))
        )}
      </PopoverContent>
    </Popover>
  )
}
