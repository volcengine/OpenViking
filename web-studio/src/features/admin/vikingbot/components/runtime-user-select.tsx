import { FilterSelect } from '#/shared/components/filter-select'
import { getBotUsers } from '#/features/admin/vikingbot/api'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { Button } from '#/shared/ui/button'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export function useRuntimeUserSelection(enabled: boolean) {
  const { identityScopeKey, serverMode } = useAppConnection()
  const [userId, setUserId] = useState('')
  const users = useQuery({
    queryKey: ['vikingbot', identityScopeKey, serverMode, 'users'],
    queryFn: () => getBotUsers(serverMode),
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
      <div className="grid gap-3 text-sm">
        <span>{t('runtimeUser')}</span>
        <FilterSelect
          label={t('runtimeUser')}
          className="w-full"
          value={selectedUser}
          onValueChange={setUserId}
          disabled={disabled || users.isPending || users.isError}
          options={[
            { value: '', label: t(users.isPending ? 'loading' : 'selectUser') },
            ...(users.data ?? []).map((user) => ({
              value: user.user_id,
              label: user.user_id,
              disabled: !user.available,
              description: user.available ? undefined : t('userUnavailable'),
            })),
          ]}
        />
      </div>
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
            href="/admin/users"
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
