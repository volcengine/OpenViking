import type { Connection } from '#/features/admin/vikingbot/api'
import {
  getConnections,
  updateConnection,
} from '#/features/admin/vikingbot/api'
import { DeleteConnection } from '#/features/admin/vikingbot/components/delete-connection'
import { PlatformIcon } from '#/features/admin/vikingbot/components/platform-icon'
import {
  getProvider,
  providers,
  upcomingProviders,
} from '#/features/admin/vikingbot/providers/registry'
import { UserSpaceSwitcher } from '#/features/admin/users/components/user-space-switcher'
import styles from '../management-page.module.css'
import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'
import { Input } from '#/shared/ui/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '#/shared/ui/dropdown-menu'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BotIcon, EllipsisIcon, PlusIcon, SearchIcon } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

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
  const [channelFilter, setChannelFilter] = useState('feishu')
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
    <div className={styles.channels}>
      {canManage && (
        <div className={styles.toolbar}>
          <UserSpaceSwitcher canManageAccounts />
          <DropdownMenu>
            <DropdownMenuTrigger
              aria-label={t('channelFilter')}
              className={styles.platformFilter}
            >
              <PlatformIcon
                platform={channelFilter === 'all' ? 'feishu' : channelFilter}
              />
              {channelFilter === 'all'
                ? t('all')
                : t('platformBots', {
                    platform: t(providers[channelFilter].label),
                  })}
            </DropdownMenuTrigger>
            <DropdownMenuContent className="min-w-40">
              <DropdownMenuRadioGroup
                value={channelFilter}
                onValueChange={(value) => setChannelFilter(value)}
              >
                <DropdownMenuRadioItem value="all">
                  {t('all')}
                </DropdownMenuRadioItem>
                {Object.entries(providers).map(([type, entry]) => (
                  <DropdownMenuRadioItem key={type} value={type}>
                    {t('platformBots', { platform: t(entry.label) })}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>
          <p className={styles.platformHint}>{t('platformIntegrationHint')}</p>
          <div className={styles.toolbarActions}>
            <div className={styles.search}>
              <SearchIcon aria-hidden="true" />
              <Input
                aria-label={t('searchBots')}
                placeholder={t('searchBotsPlaceholder')}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <Button
              className={styles.addBot}
              aria-label={t('addBot')}
              title={t('addBot')}
              variant="ghost"
              onClick={() => setChoosing(true)}
            >
              <PlusIcon className="size-4" />
              <span className="sr-only">{t('addBot')}</span>
            </Button>
          </div>
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
      {canManage &&
        (connections.isPending ||
          (!connections.error && visibleConnections.length === 0)) && (
          <div className="space-y-2">
            {connections.isPending ? (
              <p role="status" className="text-sm text-muted-foreground">
                {t('loading')}
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">
                {t(
                  search || connections.data.length > 0
                    ? 'noMatchingBots'
                    : 'noConnectedBots',
                )}
              </p>
            )}
          </div>
        )}
      <div className={styles.grid}>
        {visibleConnections.map((connection) => {
          const entry = getProvider(connection.type)
          const Credentials = entry?.Credentials
          return (
            <div key={connection.id} className={styles.card}>
              <button
                type="button"
                className={styles.cardHeading}
                onClick={() => setEditing(connection.id)}
              >
                <span className={styles.avatar}>
                  <BotIcon aria-hidden="true" />
                </span>
                <span className={styles.identity}>
                  <span className={styles.botName}>{connection.bot_name}</span>
                  <span className={styles.user}>
                    {t('associatedUser', { user: connection.identity_user })}
                  </span>
                </span>
              </button>
              <dl className={styles.stats} title={t('statisticsUnavailable')}>
                <div>
                  <dt>{t('groupCount')}</dt>
                  <dd>—</dd>
                </div>
                <div>
                  <dt>{t('questionsLast7Days')}</dt>
                  <dd>—</dd>
                </div>
              </dl>
              {(!connection.enabled ||
                connection.status.state !== 'connected') && (
                <span className={styles.connectionState}>
                  {t(`state.${connection.enabled ? 'connecting' : 'paused'}`)}
                </span>
              )}
              <details className={styles.actions}>
                <summary
                  aria-label={t('botActions', { name: connection.bot_name })}
                >
                  <EllipsisIcon aria-hidden="true" />
                </summary>
                <div className={styles.actionMenu}>
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
              </details>
            </div>
          )
        })}
      </div>
    </div>
  )
}
