import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { resolveStudioManagementCapabilities } from '#/features/connection/lib/studio-permissions'
import { ScopeCreateAccount } from '#/features/connection/components/scope-create-account'
import { DeleteAccountButton } from '#/features/admin/users/components/delete-account-button'
import { fetchAdminAccounts } from '#/shared/api/admin'
import { ErrorMessage } from '#/shared/components/error-message'
import { showErrorToast } from '#/shared/lib/error-toast'
import { Button } from '#/shared/ui/button'
import { Input } from '#/shared/ui/input'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '#/shared/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '#/shared/ui/tooltip'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { CircleHelpIcon, LoaderCircleIcon, SearchIcon } from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'

export function DataSpacesPage() {
  const { t } = useTranslation('appShell')
  const navigate = useNavigate()
  const {
    connection,
    connectionRole,
    isConnectionRoleLoading,
    serverMode,
    identityScopeKey,
    switchManagementAccount,
  } = useAppConnection()
  const { provider } = useStudioService()
  const { canManageAccounts } = resolveStudioManagementCapabilities({
    serviceProvider: provider,
    hasControlCredential: Boolean(connection.adminApiKey.trim()),
    isRoleLoading: isConnectionRoleLoading,
    role: connectionRole,
    serverMode,
  })
  const adminConnection = { ...connection, apiKey: connection.adminApiKey }
  const accounts = useQuery({
    queryKey: ['data-spaces', identityScopeKey],
    queryFn: ({ signal }) => fetchAdminAccounts(adminConnection, signal),
    enabled: canManageAccounts,
  })
  const [search, setSearch] = React.useState('')
  const [pending, setPending] = React.useState(false)
  const rows = (accounts.data ?? []).filter(({ accountId }) =>
    accountId.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
  )
  async function openMembers(accountId: string) {
    setPending(true)
    try {
      await switchManagementAccount(accountId, { preserveProfile: true })
      await navigate({ to: '/admin/users' })
    } catch (error) {
      showErrorToast(error)
    } finally {
      setPending(false)
    }
  }
  if (isConnectionRoleLoading) return <p role="status">{t('modes.checking')}</p>
  if (!canManageAccounts) return <p>{t('spaces.unavailable')}</p>
  return (
    <section className="data-spaces-page">
      <h1 className="data-spaces-title">
        {t('spaces.title')}
        <Tooltip>
          <TooltipTrigger
            aria-label={t('spaces.description')}
            render={<button type="button" />}
          >
            <CircleHelpIcon />
          </TooltipTrigger>
          <TooltipContent>{t('spaces.description')}</TooltipContent>
        </Tooltip>
      </h1>
      <div className="data-spaces-list">
        <div className="data-spaces-toolbar">
          <p>{t('spaces.count', { count: accounts.data?.length ?? 0 })}</p>
          <div className="data-spaces-actions">
            <div className="data-spaces-search">
              <SearchIcon aria-hidden="true" />
              <Input
                aria-label={t('spaces.search')}
                placeholder={t('spaces.search')}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <ScopeCreateAccount
              toolbar
              connection={adminConnection}
              onCreated={() => {
                void accounts.refetch()
              }}
            />
          </div>
        </div>
        {accounts.isError ? (
          <div className="grid gap-3">
            <ErrorMessage error={accounts.error} />
            <Button variant="outline" onClick={() => void accounts.refetch()}>
              {t('spaces.retry')}
            </Button>
          </div>
        ) : (
          <div className="data-spaces-table">
            <Table aria-label={t('spaces.title')}>
              <TableHeader>
                <TableRow>
                  <TableHead>{t('spaces.name')}</TableHead>
                  <TableHead>{t('spaces.members')}</TableHead>
                  <TableHead>{t('spaces.createdAt')}</TableHead>
                  <TableHead>{t('spaces.actions')}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {accounts.isPending ? (
                  <TableRow>
                    <TableCell colSpan={4}>
                      <span
                        role="status"
                        className="flex items-center justify-center gap-2"
                      >
                        <LoaderCircleIcon className="size-4 animate-spin" />
                        {t('spaces.loading')}
                      </span>
                    </TableCell>
                  </TableRow>
                ) : rows.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={4}
                      className="text-center text-muted-foreground"
                    >
                      {t(search ? 'spaces.noResults' : 'spaces.empty')}
                    </TableCell>
                  </TableRow>
                ) : (
                  rows.map((account) => (
                    <TableRow key={account.accountId}>
                      <TableCell className="truncate" title={account.accountId}>
                        {account.accountId}
                      </TableCell>
                      <TableCell>
                        <button
                          type="button"
                          className="data-spaces-members"
                          disabled={pending}
                          onClick={() => void openMembers(account.accountId)}
                        >
                          {t('spaces.memberCount', {
                            count: account.userCount,
                          })}
                        </button>
                      </TableCell>
                      <TableCell>
                        {formatCreatedAt(account.createdAt)}
                      </TableCell>
                      <TableCell>
                        <DeleteAccountButton
                          iconOnly
                          accountId={account.accountId}
                          adminConnection={adminConnection}
                        />
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        )}
      </div>
    </section>
  )
}

function formatCreatedAt(value: string | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const pad = (number: number) => String(number).padStart(2, '0')
  return `${date.getFullYear()}–${pad(date.getMonth() + 1)}–${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}
