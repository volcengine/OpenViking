import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '#/components/ui/dialog'
import {
  getProvider,
  providers,
  upcomingProviders,
} from '../-providers/registry'
import { PlatformIcon } from './platform-icon'
import { DeleteConnection } from './delete-connection'
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BotIcon, PlusIcon, SearchIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { getConnections, updateConnection } from '../-api'
import type { Connection } from '../-api'

export function Channels({
  canManage,
  scope,
}: {
  canManage: boolean
  scope: string
}) {
  const { t } = useTranslation('vikingbot')
  const client = useQueryClient()
  const key = ['vikingbot', scope, 'connections']
  const connections = useQuery({
    queryKey: key,
    queryFn: getConnections,
    enabled: canManage,
    refetchInterval: 4000,
  })
  const [choosing, setChoosing] = useState(false)
  const [channelFilter, setChannelFilter] = useState('all')
  const [search, setSearch] = useState('')
  const visibleConnections = (connections.data ?? []).filter(
    (connection) =>
      (channelFilter === 'all' ||
        (connection.type ?? 'feishu') === channelFilter) &&
      `${connection.bot_name} ${connection.identity_user}`
        .toLowerCase()
        .includes(search.trim().toLowerCase()),
  )
  const [editing, setEditing] = useState<string>()
  const [newType, setNewType] = useState('feishu')
  const selected = connections.data?.find((c) => c.id === editing)
  function changed(value: Connection) {
    client.setQueryData<Connection[]>(key, (old) => [
      ...(old ?? []).filter((c) => c.id !== value.id),
      value,
    ])
    setEditing(value.id)
    void client.invalidateQueries({ queryKey: key })
  }
  const mutation = useMutation({
    mutationFn: ({
      connection,
      action,
    }: {
      connection: Connection
      action: 'pause' | 'resume'
    }) => updateConnection(connection, action),
    onSuccess: () => client.invalidateQueries({ queryKey: key }),
  })
  const provider = getProvider(editing === 'new' ? newType : selected?.type)
  const Setup = provider?.Setup
  if (canManage && editing && Setup)
    return (
      <Setup
        key={editing === 'new' ? 'setup' : editing}
        connection={selected}
        onChange={changed}
        onClose={() => setEditing(undefined)}
      />
    )
  return (
    <div className="mx-auto w-full min-w-0 max-w-7xl space-y-5 p-4 md:p-6 lg:p-8">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold">{t('channels')}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t('botsHint')}</p>
        </div>
        {canManage && (
          <Button onClick={() => setChoosing(true)}>
            <PlusIcon className="size-4" />
            {t('addBot')}
          </Button>
        )}
      </div>
      {canManage && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-muted/20 p-3">
          <div className="relative min-w-48 flex-1 sm:max-w-sm">
            <SearchIcon className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              className="pl-9"
              aria-label={t('searchBots')}
              placeholder={t('searchBots')}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <label className="flex items-center gap-2 text-sm text-muted-foreground">
            {t('channelFilter')}
            <select
              className="h-9 rounded-md border bg-background px-3 text-foreground"
              value={channelFilter}
              onChange={(event) => setChannelFilter(event.target.value)}
            >
              <option value="all">{t('all')}</option>
              {Object.entries(providers).map(([type, entry]) => (
                <option key={type} value={type}>
                  {t(entry.label)}
                </option>
              ))}
            </select>
          </label>
          <span className="ml-auto text-xs tabular-nums text-muted-foreground">
            {t('botCount', { count: visibleConnections.length })}
          </span>
        </div>
      )}
      {!canManage && (
        <p className="rounded-lg bg-muted p-4 text-sm">{t('adminOnly')}</p>
      )}
      {(connections.error || mutation.error) && (
        <p role="alert" className="text-sm text-destructive">
          {t('error', {
            error: (connections.error || mutation.error)?.message,
          })}
        </p>
      )}
      <Dialog open={choosing} onOpenChange={setChoosing}>
        <DialogContent className="max-h-[85svh] overflow-y-auto sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{t('addBot')}</DialogTitle>
            <DialogDescription>{t('chooseBotChannel')}</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            {Object.entries(providers).map(([type, entry]) => (
              <div
                key={type}
                className="flex min-h-16 items-center justify-between gap-4 rounded-lg border px-4 py-3"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <PlatformIcon platform={type} />
                  <h3 className="text-sm font-medium">{t(entry.label)}</h3>
                </div>
                <Button
                  size="sm"
                  disabled={!canManage}
                  onClick={() => {
                    setChoosing(false)
                    setNewType(type)
                    setEditing('new')
                  }}
                >
                  <PlusIcon className="size-4" />
                  {t(entry.addLabel)}
                </Button>
              </div>
            ))}
            {upcomingProviders.map((name) => (
              <div
                key={name}
                className="flex min-h-16 items-center justify-between gap-4 rounded-lg border px-4 py-3"
              >
                <div className="flex min-w-0 items-center gap-3">
                  <PlatformIcon platform={name} />
                  <h3 className="text-sm font-medium text-muted-foreground">
                    {name === 'DingTalk' ? t('dingtalk') : name}
                  </h3>
                </div>
                <span className="rounded-full bg-muted px-3 py-1 text-xs text-muted-foreground">
                  {t('comingSoon')}
                </span>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
      {canManage && (
        <div className="space-y-2">
          {connections.isPending ? (
            <p role="status" className="text-sm text-muted-foreground">
              {t('loading')}
            </p>
          ) : !connections.error && visibleConnections.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {t(
                search || channelFilter !== 'all'
                  ? 'noMatchingBots'
                  : 'noConnectedBots',
              )}
            </p>
          ) : null}
        </div>
      )}
      <div className="grid grid-cols-[repeat(auto-fill,minmax(min(100%,28rem),1fr))] items-start gap-4">
        {visibleConnections.map((connection) => {
          const entry = getProvider(connection.type)
          const Credentials = entry?.Credentials
          return (
            <div
              key={connection.id}
              className="min-w-0 rounded-xl border bg-card p-4 transition-colors hover:border-primary/30 sm:p-5"
            >
              <div className="flex items-start gap-3">
                <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <BotIcon className="size-5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate font-medium">
                      {connection.bot_name}
                    </h3>
                    <span
                      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs ${connection.enabled && connection.status.state === 'connected' ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400' : 'bg-muted text-muted-foreground'}`}
                    >
                      <span className="size-1.5 rounded-full bg-current" />
                      {t(
                        `state.${connection.enabled ? (connection.status.state as 'connected' | 'connecting') : 'paused'}`,
                      )}
                    </span>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {entry ? t(entry.label) : connection.type}
                    {entry && <> · {t(entry.summary(connection))}</>}
                  </p>
                  <dl className="mt-3 flex flex-wrap gap-x-6 gap-y-2 text-xs">
                    <div className="flex min-w-0 gap-2">
                      <dt className="text-muted-foreground">
                        {t('runtimeUser')}
                      </dt>
                      <dd className="truncate">{connection.identity_user}</dd>
                    </div>
                    {connection.status.last_received && (
                      <div className="flex gap-2">
                        <dt className="text-muted-foreground">
                          {t('lastReceived')}
                        </dt>
                        <dd>
                          {new Date(
                            connection.status.last_received,
                          ).toLocaleString()}
                        </dd>
                      </div>
                    )}
                    {connection.status.last_sent && (
                      <div className="flex gap-2">
                        <dt className="text-muted-foreground">
                          {t('lastSent')}
                        </dt>
                        <dd>
                          {new Date(
                            connection.status.last_sent,
                          ).toLocaleString()}
                        </dd>
                      </div>
                    )}
                  </dl>
                </div>
              </div>
              <div className="mt-4 flex flex-wrap items-center gap-1">
                <Button
                  variant="outline"
                  onClick={() => setEditing(connection.id)}
                >
                  {t(
                    connection.setup_mode !== 'qr' ||
                      connection.step >= 5 ||
                      connection.step === 4
                      ? 'viewSetup'
                      : 'continueSetup',
                  )}
                </Button>
                <Button
                  variant="ghost"
                  disabled={mutation.isPending}
                  onClick={() =>
                    mutation.mutate({
                      connection,
                      action: connection.enabled ? 'pause' : 'resume',
                    })
                  }
                >
                  {t(connection.enabled ? 'pause' : 'resume')}
                </Button>
                {entry && (
                  <entry.Settings
                    connection={connection}
                    onSaved={() => {
                      void client.invalidateQueries({ queryKey: key })
                    }}
                  />
                )}
                {Credentials && (
                  <Credentials
                    connection={connection}
                    onSaved={() => {
                      void client.invalidateQueries({ queryKey: key })
                    }}
                  />
                )}
                <DeleteConnection
                  connection={connection}
                  title={connection.bot_name}
                  onDeleted={() => {
                    client.setQueryData<Connection[]>(key, (old) =>
                      (old ?? []).filter((item) => item.id !== connection.id),
                    )
                    void client.invalidateQueries({
                      queryKey: ['vikingbot', scope],
                    })
                  }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
