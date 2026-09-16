import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle2Icon, ExternalLinkIcon, Loader2Icon } from 'lucide-react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Button } from '#/components/ui/button'
import { useAppConnection } from '#/hooks/use-app-connection'
import { Input } from '#/components/ui/input'
import { copyTextToClipboard } from '#/lib/clipboard'
import { createConnection, getBotUsers, updateConnection } from '../../-api'
import type { Connection } from '../../-api'

const RECEIVE_EVENT = 'im.message.receive_v1'
const VERIFICATION_STAGES = ['received', 'sent'] as const

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
  const [step, setStep] = useState(connection?.step ?? 0)
  const [appId, setAppId] = useState('')
  const [secret, setSecret] = useState('')
  const { identityScopeKey } = useAppConnection()
  const users = useQuery({
    queryKey: ['vikingbot', identityScopeKey, 'users'],
    queryFn: getBotUsers,
    enabled: !connection,
    retry: false,
  })
  const [userId, setUserId] = useState('')
  const [copied, setCopied] = useState(false)
  const mutation = useMutation({
    mutationFn: async ({ action, next }: { action: string; next?: number }) => {
      if (action === 'create') {
        return createConnection({
          type: 'feishu',
          app_id: appId,
          app_secret: secret,
          user_id: userId,
        })
      }
      if (!connection) throw new Error(t('connectionError'))
      return updateConnection(connection, action, next)
    },
    onSuccess: (value, variables) => {
      onChange(value)
      if (variables.action === 'create') {
        setSecret('')
        setUserId('')
        setStep(2)
      }
      if (variables.next !== undefined) setStep(variables.next)
    },
  })
  function advance(next: number) {
    if (connection) mutation.mutate({ action: 'step', next })
    else setStep(next)
  }
  const verification = connection?.status.verification
  const steps = t('steps', { returnObjects: true }) as string[]
  return (
    <section className="mx-auto w-full min-w-0 max-w-4xl px-6 py-8 sm:px-8 lg:px-12 lg:py-10">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">{t('setupTitle')}</h2>
          <p className="mt-2 text-sm text-muted-foreground">{t('setupHint')}</p>
        </div>
        <Button variant="ghost" onClick={onClose}>
          {t('close')}
        </Button>
      </div>
      <div className="grid gap-6 md:grid-cols-[180px_minmax(0,1fr)]">
        <ol className="flex gap-2 overflow-x-auto md:flex-col">
          {steps.map((label, index) => (
            <li
              key={label}
              aria-current={step === index ? 'step' : undefined}
              className={`shrink-0 rounded-lg px-3 py-2 text-sm ${step === index ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground'}`}
            >
              {index + 1}. {label}
            </li>
          ))}
        </ol>
        <div className="space-y-5 rounded-xl border p-6 sm:p-8">
          <h3 className="font-semibold">{steps[step]}</h3>
          {connection && (
            <p className="text-sm text-muted-foreground">
              {t('connectedAs', {
                name: connection.bot_name,
                user: connection.identity_user,
              })}{' '}
              ·{' '}
              {t(
                `state.${connection.enabled ? (connection.status.state as 'connected' | 'connecting') : 'paused'}`,
              )}
            </p>
          )}
          {step === 0 && (
            <>
              <p className="text-sm leading-7">{t('createHint')}</p>
              <a
                className="inline-flex items-center gap-2 text-sm text-primary underline"
                href="https://open.feishu.cn/app"
                target="_blank"
                rel="noreferrer"
              >
                {t('openPlatform')}
                <ExternalLinkIcon className="size-4" />
              </a>
            </>
          )}
          {step === 1 && (
            <>
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
                  value={userId}
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
                  {users.error.message}{' '}
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
            </>
          )}
          {step === 2 && (
            <>
              <p className="text-sm leading-7">{t('permissionsHint')}</p>
              <p className="text-sm leading-7">{t('eventsHint')}</p>
              <code className="block rounded bg-muted p-3 text-sm">
                {RECEIVE_EVENT}
              </code>
              <a
                className="text-sm text-primary underline"
                href="https://open.feishu.cn/document/server-docs/im-v1/message/events/receive"
                target="_blank"
                rel="noreferrer"
              >
                {t('officialDocs')}
              </a>
              <p className="text-xs leading-6 text-muted-foreground">
                {t('permissionsNote')}
              </p>
            </>
          )}
          {step === 3 && (
            <p className="text-sm leading-7">{t('publishHint')}</p>
          )}
          {step === 4 && (
            <>
              <p className="text-sm leading-7">{t('groupHint')}</p>
              <Button
                variant="outline"
                disabled={mutation.isPending}
                onClick={() => mutation.mutate({ action: 'verify' })}
              >
                {t('test')}
              </Button>
              {verification && (
                <div className="space-y-3 rounded-lg bg-muted p-4">
                  <p className="break-words font-mono text-sm">
                    {t('testText', { code: verification.code })}
                  </p>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={async () => {
                      await copyTextToClipboard(
                        t('testText', { code: verification.code }),
                      )
                      setCopied(true)
                    }}
                  >
                    {t(copied ? 'copied' : 'copy')}
                  </Button>
                  {VERIFICATION_STAGES.map((key) => (
                    <p
                      key={key}
                      role="status"
                      className="flex items-center gap-2 text-sm"
                    >
                      {verification[key] && (
                        <CheckCircle2Icon className="size-4 text-green-600" />
                      )}
                      {t(key)} · {t(verification[key] ? 'verified' : 'waiting')}
                    </p>
                  ))}
                  {verification.expires_at * 1000 < Date.now() &&
                    !verification.sent && (
                      <p className="text-sm text-destructive">{t('expired')}</p>
                    )}
                </div>
              )}
              <p className="text-xs leading-6 text-muted-foreground">
                {t('troubleshoot')}
              </p>
            </>
          )}
          {step === 5 && (
            <>
              <CheckCircle2Icon className="size-10 text-green-600" />
              <h3 className="font-medium">{t('done')}</h3>
              <p className="text-sm leading-7">{t('doneHint')}</p>
            </>
          )}
          {mutation.error && (
            <p role="alert" className="text-sm text-destructive">
              {t('error', { error: mutation.error.message })}
            </p>
          )}
          <div className="flex flex-wrap justify-between gap-3 border-t pt-4">
            <Button
              variant="ghost"
              disabled={
                step === 0 ||
                mutation.isPending ||
                (Boolean(connection) && step === 2)
              }
              onClick={() => setStep(step - 1)}
            >
              {t('back')}
            </Button>
            {step === 1 ? (
              <Button
                disabled={
                  !appId.trim() ||
                  !secret.trim() ||
                  !userId.trim() ||
                  mutation.isPending
                }
                onClick={() => mutation.mutate({ action: 'create' })}
              >
                {mutation.isPending && (
                  <Loader2Icon className="size-4 animate-spin" />
                )}
                {t('connect')}
              </Button>
            ) : step === 5 ? (
              <Button onClick={onClose}>{t('finish')}</Button>
            ) : (
              <Button
                disabled={
                  mutation.isPending || (step === 4 && !verification?.sent)
                }
                onClick={() => advance(step + 1)}
              >
                {t(
                  step === 3 ? 'publishDone' : step === 4 ? 'visible' : 'next',
                )}
              </Button>
            )}
          </div>
        </div>
      </div>
    </section>
  )
}
