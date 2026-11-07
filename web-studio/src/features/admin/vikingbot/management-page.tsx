import {
  getCapabilities,
  getConnections,
  getConversations,
} from '#/features/admin/vikingbot/api'
import { Channels } from '#/features/admin/vikingbot/components/channels'
import { PlatformHistory } from '#/features/admin/vikingbot/components/platform-history'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { ConversationRow } from '#/shared/components/conversation-row'
import { Button } from '#/shared/ui/button'
import { Input } from '#/shared/ui/input'
import { useQueries, useQuery } from '@tanstack/react-query'
import { ArrowLeftIcon, BotIcon, UsersIcon } from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

const MANAGEMENT_TABS = ['channels', 'conversations'] as const
const START_COMMAND = 'openviking-server --with-bot'

export function BotManagementPage() {
  const { t } = useTranslation('appShell')
  const { connectionRole, identityScopeKey } = useAppConnection()
  if (connectionRole !== 'root') return <p>{t('modes.rootRequired')}</p>
  return (
    <BotManagementWorkspace key={identityScopeKey} scope={identityScopeKey} />
  )
}

function BotManagementWorkspace({ scope }: { scope: string }) {
  const { t } = useTranslation('vikingbot')
  const [tab, setTab] = useState<'channels' | 'conversations'>('channels')
  const [selected, setSelected] = useState<{ id: string; connection: string }>()
  const [search, setSearch] = useState('')
  const capabilities = useQuery({
    queryKey: ['vikingbot', scope, 'capabilities'],
    queryFn: getCapabilities,
    retry: false,
  })
  if (capabilities.isPending) return <p role="status">{t('loading')}</p>
  if (capabilities.isError)
    return (
      <div role="alert">
        <p>
          {t('operationFailed')} {capabilities.error.message}
        </p>
        <Button onClick={() => void capabilities.refetch()}>
          {t('retry')}
        </Button>
      </div>
    )
  if (!capabilities.data.enabled)
    return (
      <section className="mx-auto grid max-w-xl gap-4 rounded-lg border p-6">
        <h1 className="text-xl font-semibold">{t('enable')}</h1>
        <p>{t('enableHint')}</p>
        <code className="rounded-lg bg-muted p-4">{START_COMMAND}</code>
        <p className="text-sm text-muted-foreground">{t('modelHint')}</p>
        <Button onClick={() => void capabilities.refetch()}>
          {t('retry')}
        </Button>
      </section>
    )
  if (!capabilities.data.can_manage) return <p>{t('adminOnly')}</p>
  return (
    <section className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 flex-col items-start gap-4 border-b pb-3">
        <h1 className="flex items-center gap-2 text-xl font-semibold">
          <BotIcon className="size-5" />
          {t('managementTitle')}
        </h1>
        <p className="text-sm text-muted-foreground">{t('managementHint')}</p>
        <div
          role="tablist"
          aria-label={t('managementTitle')}
          className="flex gap-2"
        >
          {MANAGEMENT_TABS.map((value) => (
            <Button
              key={value}
              role="tab"
              aria-selected={tab === value}
              variant={tab === value ? 'secondary' : 'ghost'}
              onClick={() => setTab(value)}
            >
              {t(value)}
            </Button>
          ))}
        </div>
      </header>
      {tab === 'channels' ? (
        <div className="min-h-0 flex-1 overflow-auto">
          <Channels canManage scope={scope} />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 pt-3">
          <aside
            className={`${selected ? 'hidden md:flex' : 'flex'} w-full flex-col gap-3 border-r pr-3 md:w-72`}
          >
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={t('search')}
              aria-label={t('search')}
            />
            <PlatformConversations
              scope={scope}
              search={search}
              selected={selected}
              onSelect={setSelected}
            />
          </aside>
          <main
            className={`${selected ? 'flex' : 'hidden md:flex'} min-w-0 flex-1 flex-col`}
          >
            {selected ? (
              <>
                <Button
                  className="self-start md:hidden"
                  variant="ghost"
                  onClick={() => setSelected(undefined)}
                >
                  <ArrowLeftIcon />
                  {t('back')}
                </Button>
                <PlatformHistory
                  key={`${selected.connection}:${selected.id}`}
                  scope={scope}
                  connection={selected.connection}
                  conversation={selected.id}
                />
              </>
            ) : (
              <p className="m-auto p-6 text-sm text-muted-foreground">
                {t('noHistory')}
              </p>
            )}
          </main>
        </div>
      )}
    </section>
  )
}

function PlatformConversations({
  scope,
  search,
  selected,
  onSelect,
}: {
  scope: string
  search: string
  selected?: { id: string; connection: string }
  onSelect: (value: { id: string; connection: string }) => void
}) {
  const { t } = useTranslation('vikingbot')
  const connections = useQuery({
    queryKey: ['vikingbot', scope, 'connections'],
    queryFn: getConnections,
  })
  const histories = useQueries({
    queries: (connections.data ?? []).map((connection) => ({
      queryKey: ['vikingbot', scope, connection.id, 'conversations'],
      queryFn: () => getConversations(connection.id),
      refetchInterval: 5000,
    })),
  })
  const rows = histories
    .flatMap((query, index) =>
      (query.data ?? []).map((item) => ({
        ...item,
        connection: connections.data![index].id,
        channel: connections.data![index].type ?? 'feishu',
      })),
    )
    .filter((row) =>
      `${row.title} ${row.group_name ?? ''}`
        .toLowerCase()
        .includes(search.toLowerCase()),
    )
    .sort(
      (a, b) =>
        (Date.parse(b.time ?? '') || 0) - (Date.parse(a.time ?? '') || 0),
    )
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      {(connections.isPending ||
        histories.some((query) => query.isPending)) && (
        <p role="status">{t('loading')}</p>
      )}
      {[connections.error, ...histories.map((query) => query.error)]
        .filter(Boolean)
        .map((error, index) => (
          <p key={index} role="alert" className="text-sm text-destructive">
            {t('operationFailed')} {error?.message}
          </p>
        ))}
      {rows.map((row) => (
        <ConversationRow
          key={`${row.connection}:${row.conversation}`}
          title={row.title || t('newChat')}
          subtitle={`${t(row.channel)}${row.group_name ? ` · ${row.group_name}` : ''}`}
          time={row.time}
          icon={<UsersIcon className="size-4" />}
          selected={
            selected?.connection === row.connection &&
            selected.id === row.conversation
          }
          onSelect={() =>
            onSelect({ id: row.conversation, connection: row.connection })
          }
        />
      ))}
      {connections.isSuccess &&
        histories.every((query) => query.isSuccess) &&
        !rows.length && (
          <p className="p-3 text-sm text-muted-foreground">{t('noHistory')}</p>
        )}
    </div>
  )
}
