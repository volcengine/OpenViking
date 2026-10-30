import {
  fetchConnectionHealth,
  isUsableServerHealth,
  readServerIdentity,
} from '#/features/connection/lib/health'
import { StudioSetupRequestError } from '#/features/connection/lib/onboarding'
import type { ConnectionDraft } from '#/features/connection/lib/types'
import type {
  AdminAccount,
  AdminConnection,
  AdminUser,
} from '#/shared/api/admin'
import { fetchAdminAccounts, fetchAdminUserDirectory } from '#/shared/api/admin'
import { Button } from '#/shared/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/shared/ui/select'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

export function SetupIdentityDirectory({
  connection,
  disabled,
  onSelect,
}: {
  connection: ConnectionDraft
  disabled: boolean
  onSelect: (identity: { accountId: string; userId: string }) => void
}) {
  const { t } = useTranslation('onboarding')
  const [accounts, setAccounts] = useState<AdminAccount[]>([])
  const [users, setUsers] = useState<AdminUser[]>([])
  const [role, setRole] = useState<'root' | 'admin' | null>(null)
  const [loadedAccount, setLoadedAccount] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const revision = useRef(0)
  const controller = useRef<AbortController | null>(null)
  const scope = `${connection.baseUrl}\n${connection.adminApiKey}`
  const currentScope = useRef(scope)
  currentScope.current = scope
  useEffect(() => {
    revision.current += 1
    controller.current?.abort()
    setAccounts([])
    setUsers([])
    setRole(null)
    setLoadedAccount('')
    setLoading(false)
    setError('')
    return () => {
      revision.current += 1
      controller.current?.abort()
    }
  }, [scope])

  const adminConnection: AdminConnection = {
    baseUrl: connection.baseUrl,
    apiKey: connection.adminApiKey.trim(),
    accountId: connection.accountId,
    userId: connection.userId,
  }

  async function loadDirectory() {
    const request = ++revision.current
    const sourceScope = scope
    controller.current?.abort()
    controller.current = new AbortController()
    const signal = controller.current.signal
    const current = () =>
      request === revision.current &&
      sourceScope === currentScope.current &&
      !signal.aborted
    setLoading(true)
    setError('')
    setRole(null)
    setAccounts([])
    setUsers([])
    try {
      // The directory is authorized only by the control key, never by a stale
      // User API Key from a previously selected identity.
      const health = await fetchConnectionHealth(
        { ...connection, apiKey: '' },
        signal,
        'control',
      )
      if (!current()) return
      if (
        !isUsableServerHealth(health) ||
        (health.auth_mode !== 'trusted' &&
          health.role !== 'root' &&
          health.role !== 'admin')
      ) {
        setError(t('directoryControlRequired'))
        return
      }
      // In trusted mode /health describes the selected data user, not the
      // control key. Like detectConnectionIdentity, verify Root capability by
      // actually listing accounts; a failed probe must not fall back to the
      // data user's admin role and incorrectly pin the directory to one tenant.
      const listRootAccounts =
        health.auth_mode === 'trusted' || health.role === 'root'
      const directoryRole = listRootAccounts ? 'root' : 'admin'
      const identity = readServerIdentity(health)
      const scopedConnection = {
        ...adminConnection,
        accountId:
          directoryRole === 'admin' ? identity.accountId : connection.accountId,
      }
      if (directoryRole === 'admin' && !scopedConnection.accountId) {
        setError(t('directoryControlRequired'))
        return
      }
      const nextAccounts = listRootAccounts
        ? await fetchAdminAccounts(scopedConnection)
        : [{ accountId: identity.accountId, userCount: 0 }]
      if (!current()) return
      setAccounts(nextAccounts)
      setRole(directoryRole)
      // Loading a list never silently switches the selected data identity.
      const account = nextAccounts.find(
        (item) => item.accountId === connection.accountId,
      )?.accountId
      if (account) {
        const nextUsers = await fetchAdminUserDirectory(
          scopedConnection,
          account,
        )
        if (!current()) return
        setUsers(nextUsers)
        setLoadedAccount(account)
      } else {
        setLoadedAccount('')
      }
    } catch (cause) {
      if (current())
        setError(
          `${t('directoryLoadFailed')} ${new StudioSetupRequestError('data', cause, connection).message}`,
        )
    } finally {
      if (current()) setLoading(false)
    }
  }

  async function selectAccount(accountId: string) {
    if (!accounts.some((account) => account.accountId === accountId)) return
    const request = ++revision.current
    const sourceScope = scope
    const current = () =>
      request === revision.current && sourceScope === currentScope.current
    setUsers([])
    setLoadedAccount('')
    setLoading(true)
    setError('')
    onSelect({ accountId, userId: '' })
    try {
      const nextUsers = await fetchAdminUserDirectory(
        { ...adminConnection, accountId },
        accountId,
      )
      if (!current()) return
      setUsers(nextUsers)
      setLoadedAccount(accountId)
    } catch (cause) {
      if (current())
        setError(
          `${t('directoryLoadFailed')} ${new StudioSetupRequestError('data', cause, connection).message}`,
        )
    } finally {
      if (current()) setLoading(false)
    }
  }

  useEffect(() => {
    void loadDirectory()
    // This component mounts only after successful connection verification.
    // Scope changes invalidate all outstanding requests in the cleanup above.
  }, [scope])

  return (
    <div className="space-y-3 rounded-lg border border-border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">{t('directoryHint')}</p>
        {(loading || error) && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={disabled || loading || !connection.adminApiKey.trim()}
            onClick={() => void loadDirectory()}
          >
            {t(loading ? 'loadingUsers' : 'loadDirectory')}
          </Button>
        )}
      </div>
      {role && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1">
            <p className="text-xs font-medium">{t('directoryAccount')}</p>
            <Select
              value={
                accounts.some((a) => a.accountId === connection.accountId)
                  ? connection.accountId
                  : null
              }
              onValueChange={(value) => {
                if (value) void selectAccount(value)
              }}
              disabled={disabled || loading}
            >
              <SelectTrigger
                aria-label={t('directoryAccount')}
                className="h-10 w-full"
              >
                <SelectValue placeholder={t('directoryAccount')} />
              </SelectTrigger>
              <SelectContent>
                {accounts.map((account) => (
                  <SelectItem key={account.accountId} value={account.accountId}>
                    {account.accountId}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1">
            <p className="text-xs font-medium">{t('directoryUser')}</p>
            <Select
              value={
                loadedAccount === connection.accountId &&
                users.some((user) => user.userId === connection.userId)
                  ? connection.userId
                  : null
              }
              onValueChange={(value) => {
                if (
                  value &&
                  loadedAccount === connection.accountId &&
                  users.some((user) => user.userId === value)
                )
                  onSelect({ accountId: connection.accountId, userId: value })
              }}
              disabled={
                disabled ||
                loading ||
                loadedAccount !== connection.accountId ||
                users.length === 0
              }
            >
              <SelectTrigger
                aria-label={t('directoryUser')}
                className="h-10 w-full"
              >
                <SelectValue placeholder={t('directoryUser')} />
              </SelectTrigger>
              <SelectContent>
                {users.map((user) => (
                  <SelectItem key={user.userId} value={user.userId}>
                    {user.userId}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <p className="text-xs text-muted-foreground sm:col-span-2">
            {t(role === 'admin' ? 'directoryAdminHint' : 'directoryKeyHint')}
          </p>
          {!loading && loadedAccount && users.length === 0 && (
            <p className="text-xs text-muted-foreground sm:col-span-2">
              {t('noUsers')}
            </p>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
