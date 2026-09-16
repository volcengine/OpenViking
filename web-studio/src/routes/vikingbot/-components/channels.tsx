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
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CableIcon, PlusIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
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
      action: string
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
    <div className="w-full min-w-0 space-y-6 p-4 md:p-6">
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
        <label className="flex items-center gap-3 text-sm">
          {t('channelFilter')}
          <select
            className="rounded-md border bg-background px-3 py-2"
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
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('addBot')}</DialogTitle>
            <DialogDescription>{t('chooseBotChannel')}</DialogDescription>
          </DialogHeader>
          <div className="grid gap-3 sm:grid-cols-2">
            {Object.entries(providers).map(([type, entry]) => (
              <div
                key={type}
                className="flex min-h-36 flex-col items-start justify-between gap-4 rounded-xl border p-5"
              >
                <div className="space-y-2">
                  <h3 className="font-medium">{t(entry.label)}</h3>
                  <p className="text-sm text-muted-foreground">
                    {t('platformReady')}
                  </p>
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
                className="flex min-h-36 flex-col items-start justify-between gap-4 rounded-xl border p-5"
              >
                <h3 className="font-medium">
                  {name === 'DingTalk' ? t('dingtalk') : name}
                </h3>
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
          ) : !connections.error &&
            !connections.data.some(
              (connection) =>
                channelFilter === 'all' ||
                (connection.type ?? 'feishu') === channelFilter,
            ) ? (
            <p className="text-sm text-muted-foreground">
              {t('noConnectedBots')}
            </p>
          ) : null}
        </div>
      )}
      {connections.data
        ?.filter(
          (connection) =>
            channelFilter === 'all' ||
            (connection.type ?? 'feishu') === channelFilter,
        )
        .map((connection) => {
          const entry = getProvider(connection.type)
          const Credentials = entry?.Credentials
          return (
            <div
              key={connection.id}
              className="space-y-3 rounded-xl border p-5"
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h3 className="flex items-center gap-2 font-medium">
                  <CableIcon className="size-5 text-primary" />
                  {connection.bot_name}
                </h3>
                <span className="rounded-full bg-muted px-3 py-1 text-xs">
                  {t(
                    `state.${connection.enabled ? (connection.status.state as 'connected' | 'connecting') : 'paused'}`,
                  )}
                </span>
              </div>
              <p className="text-sm text-muted-foreground">
                {entry ? t(entry.label) : connection.type} · {t('onlyMention')}
              </p>
              {connection.status.last_received && (
                <p className="text-xs text-muted-foreground">
                  {t('lastReceived')}:{' '}
                  {new Date(connection.status.last_received).toLocaleString()}
                </p>
              )}
              {connection.status.last_sent && (
                <p className="text-xs text-muted-foreground">
                  {t('lastSent')}:{' '}
                  {new Date(connection.status.last_sent).toLocaleString()}
                </p>
              )}
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  onClick={() => setEditing(connection.id)}
                >
                  {t(
                    connection.step >= 5
                      ? 'viewSetup'
                      : connection.setup_mode === 'qr' && connection.step === 4
                        ? 'qr.addGroup'
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
              </div>
              {Credentials && (
                <Credentials
                  connection={connection}
                  onSaved={() => {
                    void client.invalidateQueries({ queryKey: key })
                  }}
                />
              )}
            </div>
          )
        })}
    </div>
  )
}
