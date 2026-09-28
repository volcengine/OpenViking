import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { useAppConnection } from '#/hooks/use-app-connection'
import { getBotUsers } from '../-api'

export function useRuntimeUserSelection(enabled: boolean) {
  const { identityScopeKey } = useAppConnection()
  const [userId, setUserId] = useState('')
  const users = useQuery({
    queryKey: ['vikingbot', identityScopeKey, 'users'],
    queryFn: getBotUsers,
    enabled,
    retry: false,
  })
  const available = users.isSuccess
    ? users.data.filter((user) => user.available)
    : []
  const selectedUser = available.some((user) => user.user_id === userId)
    ? userId
    : available.length === 1
      ? available[0].user_id
      : ''
  return { users, available, selectedUser, setUserId }
}

export function RuntimeUserSelect({
  selection,
  disabled = false,
}: {
  selection: ReturnType<typeof useRuntimeUserSelection>
  disabled?: boolean
}) {
  const { t } = useTranslation('vikingbot')
  const { users, available, selectedUser, setUserId } = selection
  return (
    <div className="space-y-3">
      <label className="grid gap-3 text-sm">
        <span>{t('runtimeUser')}</span>
        <select
          aria-label={t('runtimeUser')}
          className="h-9 w-full rounded-md border bg-background px-3 text-sm"
          value={selectedUser}
          disabled={disabled || users.isPending || users.isError}
          onChange={(event) => setUserId(event.target.value)}
        >
          <option value="">
            {t(users.isPending ? 'loading' : 'selectUser')}
          </option>
          {users.data?.map((user) => (
            <option
              key={user.user_id}
              value={user.user_id}
              disabled={!user.available}
            >
              {user.user_id}
              {user.available ? '' : ` — ${t('userUnavailable')}`}
            </option>
          ))}
        </select>
      </label>
      <p className="text-xs leading-6 text-muted-foreground">
        {t('runtimeUserHint')}
      </p>
      {users.error && (
        <p role="alert" className="text-sm text-destructive">
          {t('operationFailed')} {users.error.message}
        </p>
      )}
      {users.isSuccess && available.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {t('noUsers')}{' '}
          <a
            href="/users"
            target="_blank"
            rel="noreferrer"
            className="text-primary underline"
          >
            {t('manageUsers')}
          </a>
        </p>
      )}
      {(users.isError || (users.isSuccess && available.length === 0)) && (
        <Button
          variant="ghost"
          size="sm"
          disabled={disabled || users.isFetching}
          onClick={() => void users.refetch()}
        >
          {t('retry')}
        </Button>
      )}
    </div>
  )
}
