import { ReplyMode } from './reply-settings'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ExternalLinkIcon, Loader2Icon } from 'lucide-react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Button } from '#/components/ui/button'
import { useAppConnection } from '#/hooks/use-app-connection'
import { Input } from '#/components/ui/input'
import { createConnection, getBotUsers } from '../../-api'
import { GroupSetup } from './group-setup'
import type { Connection } from '../../-api'

export function FeishuSetup({
  connection,
  onChange,
  onClose,
}: {
  connection?: Connection
  onChange: (connection: Connection) => void
  onClose: () => void
}) {
  const { t } = useTranslation('vikingbot')
  const { identityScopeKey } = useAppConnection()
  const [appId, setAppId] = useState('')
  const [secret, setSecret] = useState('')
  const [userId, setUserId] = useState('')
  const [requireMention, setRequireMention] = useState(true)
  const [created, setCreated] = useState<Connection>()
  const current = connection ?? created
  const users = useQuery({
    queryKey: ['vikingbot', identityScopeKey, 'users'],
    queryFn: getBotUsers,
    enabled: !current,
    retry: false,
  })
  const available = users.data?.filter((user) => user.available) ?? []
  const selectedUser =
    userId || (available.length === 1 ? available[0].user_id : '')
  const mutation = useMutation({
    mutationFn: () =>
      createConnection({
        type: 'feishu',
        credentials: { app_id: appId.trim(), app_secret: secret.trim() },
        user_id: selectedUser,
        settings: { thread_require_mention: requireMention },
      }),
    onSuccess: (value) => {
      setSecret('')
      setCreated(value)
      onChange(value)
    },
  })
  return (
    <section className="mx-auto w-full min-w-0 max-w-3xl space-y-6 px-6 py-8 sm:px-8">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">{t('manualTitle')}</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            {t('manualHint')}
          </p>
        </div>
        <Button variant="ghost" onClick={onClose}>
          {t('qr.close')}
        </Button>
      </div>
      {current ? (
        <div className="rounded-xl border p-6 sm:p-8">
          <GroupSetup
            connection={current}
            onChange={onChange}
            onClose={onClose}
            manual
          />
        </div>
      ) : (
        <div className="space-y-6 rounded-xl border p-6 sm:p-8">
          <p className="text-sm leading-7">{t('credentialsHint')}</p>
          <label className="grid gap-3 text-sm">
            <span>{t('appId')}</span>
            <Input
              value={appId}
              onChange={(e) => setAppId(e.target.value)}
              autoComplete="off"
            />
          </label>
          <label className="grid gap-3 text-sm">
            <span>{t('appSecret')}</span>
            <Input
              type="password"
              value={secret}
              onChange={(e) => setSecret(e.target.value)}
              autoComplete="new-password"
            />
          </label>
          <label className="grid gap-3 text-sm">
            <span>{t('runtimeUser')}</span>
            <select
              aria-label={t('runtimeUser')}
              className="h-9 w-full rounded-md border bg-background px-3 text-sm"
              value={selectedUser}
              disabled={users.isPending || users.isError}
              onChange={(e) => setUserId(e.target.value)}
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
              {t('operationFailed')} {users.error.message}{' '}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void users.refetch()}
              >
                {t('retry')}
              </Button>
            </p>
          )}
          {!users.isPending && !users.isError && !users.data.length && (
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
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void users.refetch()}
              >
                {t('retry')}
              </Button>
            </p>
          )}

          <ReplyMode
            value={requireMention}
            onChange={setRequireMention}
            disabled={mutation.isPending}
          />
          <details className="rounded-lg border p-4">
            <summary className="cursor-pointer text-sm font-medium">
              {t('manualInstructions')}
            </summary>
            <div className="mt-4 space-y-3 text-sm leading-7 text-muted-foreground">
              <p>{t('permissionsHint')}</p>
              <p>{t('eventsHint')}</p>
              <p>{t('publishHint')}</p>
              <a
                className="inline-flex items-center gap-2 text-primary underline"
                href="https://open.feishu.cn/app"
                target="_blank"
                rel="noreferrer"
              >
                {t('openPlatform')}
                <ExternalLinkIcon className="size-4" />
              </a>
            </div>
          </details>
          {mutation.error && (
            <p role="alert" className="text-sm text-destructive">
              {t('operationFailed')} {mutation.error.message}
            </p>
          )}
          <Button
            disabled={
              !appId.trim() ||
              !secret.trim() ||
              !selectedUser ||
              mutation.isPending
            }
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending && (
              <Loader2Icon className="size-4 animate-spin" />
            )}
            {t('connect')}
          </Button>
        </div>
      )}
    </section>
  )
}
