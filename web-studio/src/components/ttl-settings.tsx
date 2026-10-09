import { useId, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useTtlManagement } from '#/hooks/use-ttl-management'
import { isTtlRoot, ttlScopes } from '#/lib/ttl'
import type { TtlConfig, TtlPolicy, TtlScope } from '#/lib/ttl'
import { Button } from '#/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '#/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { Input } from '#/components/ui/input'

type Management = ReturnType<typeof useTtlManagement>
const selectClass = 'h-9 w-full rounded-md border bg-background px-3 text-sm'
const scopes = [...ttlScopes, 'directory']
const globalModes = ['server', 'disabled', 'days']
const overrideModes = ['server', 'inherit', 'disabled', 'days', 'absolute']
const initialModes = ['server', 'disabled', 'bestPractice', 'days']

export function InitialTtlSettings({
  value,
  onChange,
}: {
  value?: TtlConfig
  onChange: (value?: TtlConfig) => void
}) {
  const { t } = useTranslation('settings')
  const mode = !value
    ? 'server'
    : value.global?.mode === 'days'
      ? 'days'
      : value.sessions?.mode === 'days'
        ? 'bestPractice'
        : 'disabled'
  const defaults = (next: string, days = 30): TtlConfig | undefined =>
    next === 'server'
      ? undefined
      : {
          global:
            next === 'days'
              ? { mode: 'days', ttl_days: days }
              : { mode: 'disabled' },
          ...Object.fromEntries(
            ttlScopes
              .slice(1)
              .map((scope) => [
                scope,
                next === 'bestPractice'
                  ? { mode: 'days', ttl_days: scope === 'sessions' ? 30 : 60 }
                  : { mode: next === 'days' ? 'inherit' : 'disabled' },
              ]),
          ),
        }
  return (
    <fieldset className="space-y-3">
      <label className="block space-y-2 text-sm">
        <span>{t('ttl.initialPolicy')}</span>
        <select
          className={selectClass}
          value={mode}
          onChange={(e) => onChange(defaults(e.target.value))}
        >
          {initialModes.map((key) => (
            <option key={key} value={key}>
              {t(`ttl.modes.${key}`)}
            </option>
          ))}
        </select>
      </label>
      {mode === 'days' && (
        <label className="block space-y-2 text-sm">
          <span>{t('ttl.days')}</span>
          <Input
            type="number"
            min={1}
            max={365000}
            step={1}
            required
            value={value?.global?.ttl_days || ''}
            onChange={(e) => onChange(defaults('days', Number(e.target.value)))}
          />
        </label>
      )}
      <p className="text-sm text-muted-foreground">{t('ttl.initialHint')}</p>
    </fieldset>
  )
}

export function LibraryTtlSettings() {
  const state = useTtlManagement()
  const { t } = useTranslation('settings')
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {t('ttl.title', { account: state.connection.accountId })}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {state.allowed ? (
          <LibraryEditor key={state.scopeKey} state={state} />
        ) : (
          <p className="text-sm text-muted-foreground">
            {t('ttl.unavailable')}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function LibraryEditor({ state }: { state: Management }) {
  const { t } = useTranslation('settings')
  const id = useId()
  const [scope, setScope] = useState<string>('global')
  const [uri, setUri] = useState(
    `viking://user/${state.connection.userId}/memories/events`,
  )
  const target = scope === 'directory' ? uri.trim().replace(/\/+$/, '') : scope
  return (
    <div className="space-y-4">
      <label htmlFor={id} className="block space-y-2 text-sm">
        <span>{t('ttl.scope')}</span>
        <select
          id={id}
          className={selectClass}
          value={scope}
          onChange={(e) => setScope(e.target.value)}
        >
          {scopes.map((key) => (
            <option key={key} value={key}>
              {t(`ttl.scopes.${key}`)}
            </option>
          ))}
        </select>
      </label>
      {scope === 'directory' && (
        <label className="block space-y-2 text-sm">
          <span>{t('ttl.rootUri')}</span>
          <Input value={uri} onChange={(e) => setUri(e.target.value)} />
        </label>
      )}
      {scope !== 'directory' || isTtlRoot(target) ? (
        <TtlEditor key={target} target={target} state={state} />
      ) : (
        <p role="alert" className="text-sm text-destructive">
          {t('ttl.invalidRoot')}
        </p>
      )}
    </div>
  )
}

export function RootTtlSettings({ uri }: { uri: string }) {
  // Do not load management state or settings for individual objects.
  if (!isTtlRoot(uri)) return null
  return <RootControl uri={uri.replace(/\/+$/, '')} />
}

function RootControl({ uri }: { uri: string }) {
  const state = useTtlManagement()
  if (!state.allowed) return null
  return <RootDialog key={`${state.scopeKey}:${uri}`} state={state} uri={uri} />
}

function RootDialog({ state, uri }: { state: Management; uri: string }) {
  const { t } = useTranslation('settings')
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
        {t('ttl.rootAction')}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('ttl.rootAction')}</DialogTitle>
            <DialogDescription className="break-all">{uri}</DialogDescription>
          </DialogHeader>
          {open && <TtlEditor target={uri} state={state} />}
        </DialogContent>
      </Dialog>
    </>
  )
}

function TtlEditor({ target, state }: { target: string; state: Management }) {
  const { t } = useTranslation('settings')
  const config = useQuery({
    queryKey: state.configKey,
    queryFn: state.api.getConfig,
    retry: false,
  })
  if (config.isPending) return <p role="status">{t('ttl.loading')}</p>
  if (config.isError)
    return (
      <div role="alert" className="space-y-2 text-sm">
        <p>
          {t('ttl.loadFailed')}: {config.error.message}
        </p>
        <Button variant="outline" onClick={() => void config.refetch()}>
          {t('ttl.retry')}
        </Button>
      </div>
    )
  const policy = isTtlRoot(target)
    ? config.data.directories?.[target]
    : config.data[target as TtlScope]
  return <PolicyForm target={target} state={state} policy={policy} />
}

function PolicyForm({
  target,
  state,
  policy,
}: {
  target: string
  state: Management
  policy?: TtlPolicy
}) {
  const { t, i18n } = useTranslation('settings')
  const id = useId()
  const client = useQueryClient()
  const [mode, setMode] = useState(
    policy?.mode ??
      (policy?.ttl_days
        ? 'days'
        : policy?.ttl_absolute
          ? 'absolute'
          : 'server'),
  )
  const [days, setDays] = useState(String(policy?.ttl_days ?? ''))
  const [absolute, setAbsolute] = useState(() => {
    if (!policy?.ttl_absolute) return ''
    const date = new Date(policy.ttl_absolute * 1000)
    return new Date(date.getTime() - date.getTimezoneOffset() * 60000)
      .toISOString()
      .slice(0, 19)
  })
  const isRoot = isTtlRoot(target)
  const report = useQuery({
    queryKey: ['ttl-report', state.scopeKey, target],
    queryFn: () => state.api.get(target),
    enabled: isRoot,
    retry: false,
  })
  const save = useMutation({
    mutationFn: (next: TtlPolicy | null) => state.api.setPolicy(target, next),
    onSuccess: async (config) => {
      client.setQueryData(state.configKey, config)
      await client.invalidateQueries({
        predicate: ({ queryKey }) =>
          queryKey[0] === 'ttl-report' ||
          queryKey[0] === 'sessions' ||
          (typeof queryKey[0] === 'string' &&
            queryKey[0].startsWith('viking-')),
      })
    },
  })
  const timestamp = Math.floor(new Date(absolute).getTime() / 1000)
  const valid =
    mode === 'days'
      ? Number.isInteger(Number(days)) &&
        Number(days) >= 1 &&
        Number(days) <= 365000
      : mode === 'absolute'
        ? Number.isInteger(timestamp) &&
          timestamp >= 1 &&
          timestamp <= 253402300799
        : true
  const effective = report.data?.effective_policy
  const policyLabel = (value: TtlPolicy) =>
    value.mode === 'days'
      ? t('ttl.dayCount', { count: value.ttl_days })
      : value.mode === 'absolute' && value.ttl_absolute
        ? new Intl.DateTimeFormat(i18n.resolvedLanguage, {
            dateStyle: 'medium',
            timeStyle: 'short',
          }).format(new Date(value.ttl_absolute * 1000))
        : t(`ttl.modes.${value.mode ?? 'inherit'}`)
  return (
    <form
      className="space-y-4"
      onSubmit={(event) => {
        event.preventDefault()
        if (!valid || save.isPending) return
        save.mutate(
          mode === 'server'
            ? null
            : {
                mode: mode as TtlPolicy['mode'],
                ...(mode === 'days'
                  ? { ttl_days: Number(days) }
                  : mode === 'absolute'
                    ? { ttl_absolute: timestamp }
                    : {}),
              },
        )
      }}
    >
      <p className="text-sm text-muted-foreground">{t('ttl.scopeHint')}</p>
      <label htmlFor={id} className="block space-y-2 text-sm">
        <span>{t('ttl.policy')}</span>
        <select
          id={id}
          className={selectClass}
          value={mode}
          disabled={save.isPending}
          onChange={(event) => {
            setMode(event.target.value)
            save.reset()
          }}
        >
          {(target === 'global' ? globalModes : overrideModes).map((value) => (
            <option key={value} value={value}>
              {t(`ttl.modes.${value}`)}
            </option>
          ))}
        </select>
      </label>
      {mode === 'days' && (
        <label className="block space-y-2 text-sm">
          <span>{t('ttl.days')}</span>
          <Input
            type="number"
            min={1}
            max={365000}
            step={1}
            required
            value={days}
            disabled={save.isPending}
            onChange={(e) => {
              setDays(e.target.value)
              save.reset()
            }}
          />
        </label>
      )}
      {mode === 'absolute' && (
        <label className="block space-y-2 text-sm">
          <span>{t('ttl.absolute')}</span>
          <Input
            type="datetime-local"
            step={1}
            required
            value={absolute}
            disabled={save.isPending}
            onChange={(e) => {
              setAbsolute(e.target.value)
              save.reset()
            }}
          />
        </label>
      )}
      {isRoot && (
        <p className="text-sm text-muted-foreground">
          {effective
            ? t('ttl.effective', { policy: policyLabel(effective) })
            : report.isError
              ? t('ttl.effectiveUnavailable', { error: report.error.message })
              : t('ttl.loading')}
        </p>
      )}
      <p className="text-sm text-muted-foreground">{t('ttl.applyHint')}</p>
      {save.isError && (
        <div role="alert" className="text-sm text-destructive">
          <p>{t('ttl.saveFailed')}</p>
          <p className="break-words">{save.error.message}</p>
        </div>
      )}
      {save.isSuccess && (
        <p role="status" className="text-sm">
          {t('ttl.saved')}
        </p>
      )}
      <Button type="submit" disabled={!valid || save.isPending}>
        {t(save.isPending ? 'ttl.saving' : 'ttl.save')}
      </Button>
    </form>
  )
}
