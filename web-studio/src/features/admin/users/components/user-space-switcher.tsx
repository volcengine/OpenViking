import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { fetchAdminAccounts } from '#/shared/api/admin'
import { showErrorToast } from '#/shared/lib/error-toast'
import { FilterSelect } from '#/shared/components/filter-select'
import { useQuery } from '@tanstack/react-query'
import { BoxIcon } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export function UserSpaceSwitcher({
  canManageAccounts,
}: {
  canManageAccounts?: boolean
}) {
  const { t } = useTranslation('appShell')
  const {
    connection,
    identityScopeKey,
    switchManagementAccount,
    connectionRole,
    isConnectionRoleLoading,
  } = useAppConnection()
  const canSwitch =
    canManageAccounts !== false &&
    connectionRole === 'root' &&
    !isConnectionRoleLoading &&
    Boolean(connection.adminApiKey.trim())
  const [open, setOpen] = useState(false)
  const [pending, setPending] = useState(false)
  const accounts = useQuery({
    queryKey: ['data-spaces', identityScopeKey],
    queryFn: ({ signal }) =>
      fetchAdminAccounts(
        { ...connection, apiKey: connection.adminApiKey },
        signal,
      ),
    enabled: canSwitch && open,
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
  if (!canSwitch) return <div className="users-space-trigger">{label}</div>
  return (
    <FilterSelect
      label={t('adminNavigation.currentSpace', { space: connection.accountId })}
      value={connection.accountId}
      onValueChange={(value) => void choose(value)}
      icon={<BoxIcon className="size-4 shrink-0" />}
      options={(accounts.data ?? []).map((account) => ({
        value: account.accountId,
        label: account.accountId,
        icon: <BoxIcon className="size-4" />,
      }))}
      placeholder={connection.accountId}
      className="users-space-trigger"
      disabled={pending}
      open={open}
      onOpenChange={setOpen}
      loading={accounts.isPending}
      loadingLabel={t('spaces.loading')}
      error={accounts.isError}
      retryLabel={t('spaces.retry')}
      onRetry={() => void accounts.refetch()}
    />
  )
}
