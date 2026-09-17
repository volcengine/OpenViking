import { useDefaultConversationTitles } from '#/lib/sessions/use-default-conversation-titles'
import { ConversationRow } from './-components/conversation-row'
import { readPlaygroundAgentSessionIds } from '#/routes/playground/-lib/utils'
import { useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useQueries, useQuery } from '@tanstack/react-query'
import {
  ArrowLeftIcon,
  BotIcon,
  PlusIcon,
  MessageSquareIcon,
  UsersIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { useAppConnection } from '#/hooks/use-app-connection'
import {
  useBotHealth,
  useSessionListByRecency,
} from '#/lib/sessions/use-sessions'
import { useSessionTitles } from '#/lib/sessions/use-session-titles'
import { Thread } from '#/routes/sessions/-components/thread'
import { getCapabilities, getConnections, getConversations } from './-api'
import {
  createVikingBotWebSessionId,
  isVikingBotWebSession,
} from '#/lib/sessions/vikingbot-sessions'
import { DeleteConversation } from '#/components/sessions/delete-conversation'
import { Channels } from './-components/channels'
import { PlatformHistory } from './-components/platform-history'

const PAGE_TABS = ['conversations', 'channels'] as const
const START_COMMAND = 'openviking-server --with-bot'

export const Route = createFileRoute('/vikingbot/')({
  component: VikingBotPage,
})

function VikingBotPage() {
  const { identityScopeKey } = useAppConnection()
  return <VikingBotWorkspace key={identityScopeKey} scope={identityScopeKey} />
}

function VikingBotWorkspace({ scope }: { scope: string }) {
  const { t } = useTranslation('vikingbot')
  const [tab, setTab] = useState<(typeof PAGE_TABS)[number]>('conversations')
  const [selected, setSelected] = useState<{
    id: string
    connection?: string
    draft?: boolean
  }>()
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const capabilities = useQuery({
    queryKey: ['vikingbot', scope, 'capabilities'],
    queryFn: getCapabilities,
    retry: false,
  })
  const canManage = capabilities.data?.can_manage ?? false
  const health = useBotHealth()
  const connections = useQuery({
    queryKey: ['vikingbot', scope, 'connections'],
    queryFn: getConnections,
    enabled: canManage,
  })
  const hasFeishu =
    canManage &&
    connections.data?.some(
      (connection) => (connection.type ?? 'feishu') === 'feishu',
    )
  const sourceFilters = hasFeishu ? (['all', 'web', 'feishu'] as const) : []
  const activeFilter = hasFeishu ? filter : 'all'
  const sessions = useSessionListByRecency()
  const { getTitle, removeTitle } = useSessionTitles(scope)
  useDefaultConversationTitles(
    scope,
    sessions.data
      .filter((session) =>
        isVikingBotWebSession(session, readPlaygroundAgentSessionIds(scope)),
      )
      .map((session) => session.session_id),
  )
  const platformQueries = useQueries({
    queries: (connections.data ?? []).map((connection) => ({
      queryKey: ['vikingbot', scope, connection.id, 'conversations'],
      queryFn: () => getConversations(connection.id),
      refetchInterval: 5000,
      enabled: canManage,
    })),
  })
  const rows = [
    ...sessions.data
      .filter((session) =>
        isVikingBotWebSession(session, readPlaygroundAgentSessionIds(scope)),
      )
      .map((session) => ({
        id: session.session_id,
        connection: undefined as string | undefined,
        title:
          getTitle(session.session_id) === session.session_id
            ? t('newChat')
            : getTitle(session.session_id),
        time: session.mod_time,
        channel: 'web',
        groupName: '',
      })),
    ...platformQueries.flatMap((query, index) =>
      (query.data ?? []).map((item) => ({
        id: item.conversation,
        connection: connections.data![index].id,
        title: item.title || t('newChat'),
        time: item.time,
        channel: connections.data![index].type ?? 'feishu',
        groupName: item.group_name ?? '',
      })),
    ),
  ]
    .filter(
      (row) =>
        (activeFilter === 'all' || row.channel === activeFilter) &&
        `${row.title} ${row.groupName}`
          .toLowerCase()
          .includes(search.toLowerCase()),
    )
    .sort(
      (a, b) =>
        (Date.parse(b.time ?? '') || 0) - (Date.parse(a.time ?? '') || 0),
    )
  function create() {
    setSelected({ id: createVikingBotWebSessionId(), draft: true })
  }
  function select(id: string, connection?: string) {
    setSelected({ id, connection })
  }
  return (
    <div className="-mx-4 -my-6 flex h-[calc(100svh-3rem)] min-w-0 flex-col overflow-hidden md:-mx-6">
      <header className="flex shrink-0 flex-col items-start gap-4 border-b px-4 pt-4 md:px-6">
        <h1 className="flex items-center gap-2 font-semibold">
          <BotIcon className="size-5 text-primary" />
          {t('title')}
        </h1>
        <div role="tablist" aria-label={t('title')} className="flex gap-6">
          {PAGE_TABS.map((value) => (
            <button
              type="button"
              role="tab"
              aria-selected={tab === value}
              key={value}
              className={`-mb-px border-b-2 px-1 pb-3 text-sm font-medium transition-colors ${tab === value ? 'border-primary text-primary' : 'border-transparent text-muted-foreground hover:text-foreground'}`}
              onClick={() => setTab(value)}
            >
              {t(value)}
            </button>
          ))}
        </div>
      </header>
      {capabilities.isPending ? (
        <p role="status" className="p-8">
          {t('loading')}
        </p>
      ) : capabilities.error ? (
        <div role="alert" className="p-8">
          <p>
            {t('operationFailed')} {capabilities.error.message}
          </p>
          <Button onClick={() => void capabilities.refetch()}>
            {t('retry')}
          </Button>
        </div>
      ) : !capabilities.data.enabled ? (
        <div className="m-auto max-w-xl space-y-4 p-8">
          <h2 className="text-xl font-semibold">{t('enable')}</h2>
          <p>{t('enableHint')}</p>
          <code className="block rounded-lg bg-muted p-4">{START_COMMAND}</code>
          <p className="text-sm text-muted-foreground">{t('modelHint')}</p>
          <Button
            onClick={() => {
              void capabilities.refetch()
              void health.refetch()
            }}
          >
            {t('retry')}
          </Button>
        </div>
      ) : tab === 'channels' ? (
        <div className="flex-1 overflow-auto">
          <Channels canManage={canManage} scope={scope} />
        </div>
      ) : (
        <div className="flex min-h-0 flex-1">
          <aside
            className={`${selected ? 'hidden md:flex' : 'flex'} w-full shrink-0 flex-col border-r md:w-72`}
          >
            <div className="space-y-3 border-b p-3">
              <Button
                className="w-full"
                disabled={health.isError}
                onClick={() => void create()}
              >
                <PlusIcon className="size-4" />
                {t('newChat')}
              </Button>
              <Input
                aria-label={t('search')}
                placeholder={t('search')}
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              {sourceFilters.length > 0 && (
                <div className="flex gap-1">
                  {sourceFilters.map((value) => (
                    <Button
                      size="sm"
                      variant={activeFilter === value ? 'secondary' : 'ghost'}
                      key={value}
                      onClick={() => setFilter(value)}
                    >
                      {t(value)}
                    </Button>
                  ))}
                </div>
              )}
            </div>
            <div className="flex-1 overflow-auto p-2">
              {platformQueries.map(
                (query, index) =>
                  query.error && (
                    <p
                      key={index}
                      role="alert"
                      className="p-2 text-sm text-destructive"
                    >
                      {t('operationFailed')} {query.error.message}
                    </p>
                  ),
              )}
              {rows.map((row) => (
                <ConversationRow
                  key={`${row.connection ?? 'web'}:${row.id}`}
                  title={row.title}
                  subtitle={
                    row.groupName
                      ? `${t(row.channel)}-${row.groupName}`
                      : t(row.channel)
                  }
                  time={row.time}
                  icon={
                    row.connection ? (
                      <UsersIcon className="size-4" />
                    ) : (
                      <MessageSquareIcon className="size-4" />
                    )
                  }
                  selected={
                    selected?.id === row.id &&
                    selected.connection === row.connection
                  }
                  onSelect={() => select(row.id, row.connection)}
                  action={
                    !row.connection ? (
                      <DeleteConversation
                        id={row.id}
                        title={row.title}
                        onDeleted={() => {
                          removeTitle(row.id)
                          setSelected((current) =>
                            current?.id === row.id && !current.connection
                              ? undefined
                              : current,
                          )
                        }}
                      />
                    ) : undefined
                  }
                />
              ))}
              {activeFilter === 'feishu' && !canManage && (
                <p className="p-3 text-sm text-muted-foreground">
                  {t('adminOnly')}
                </p>
              )}
            </div>
          </aside>
          <main
            className={`${selected ? 'flex' : 'hidden md:flex'} min-w-0 flex-1 flex-col`}
          >
            {selected && (
              <Button
                variant="ghost"
                className="self-start md:hidden"
                onClick={() => setSelected(undefined)}
              >
                <ArrowLeftIcon className="size-4" />
                {t('back')}
              </Button>
            )}
            {health.isError && (
              <div
                role="alert"
                className="flex items-center justify-between gap-3 border-b p-3 text-sm text-destructive"
              >
                {t('connectionError')}
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void health.refetch()}
                >
                  {t('retry')}
                </Button>
              </div>
            )}
            <div className="min-h-0 flex-1">
              {selected ? (
                selected.connection ? (
                  <PlatformHistory
                    key={`${selected.connection}:${selected.id}`}
                    scope={scope}
                    connection={selected.connection}
                    conversation={selected.id}
                  />
                ) : (
                  <Thread
                    key={selected.id}
                    sessionId={selected.id}
                    draft={selected.draft}
                    onPersisted={() =>
                      setSelected((current) =>
                        current?.id === selected.id && !current.connection
                          ? { ...current, draft: false }
                          : current,
                      )
                    }
                  />
                )
              ) : (
                <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
                  <BotIcon className="size-12 text-primary" />
                  <h2 className="text-xl font-semibold">{t('empty')}</h2>
                  <p className="max-w-md text-sm text-muted-foreground">
                    {t('emptyHint')}
                  </p>
                  <Button variant="outline" onClick={() => setTab('channels')}>
                    {t('manageBots')}
                  </Button>
                </div>
              )}
            </div>
          </main>
        </div>
      )}
    </div>
  )
}
