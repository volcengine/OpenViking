import { readPlaygroundAgentSessionIds } from '#/routes/playground/-lib/utils'
import { useEffect, useRef, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeftIcon, BotIcon, PlusIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { useAppConnection } from '#/hooks/use-app-connection'
import {
  useBotHealth,
  useCreateSession,
  useSessionListByRecency,
} from '#/lib/sessions/use-sessions'
import { useSessionTitles } from '#/lib/sessions/use-session-titles'
import { Thread } from '#/routes/sessions/-components/thread'
import { getCapabilities, getConnections } from './-api'
import {
  createVikingBotWebSessionId,
  isVikingBotWebSession,
} from '#/lib/sessions/vikingbot-sessions'
import { DeleteConversation } from './-components/delete-conversation'
import { Channels } from './-components/channels'
import {
  PlatformConversationList,
  PlatformHistory,
} from './-components/platform-history'

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
  }>()
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [error, setError] = useState('')
  const creating = useRef(false)
  const mountedGeneration = useRef(0)
  useEffect(
    () => () => {
      mountedGeneration.current += 1
    },
    [],
  )
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
  const createSession = useCreateSession()
  const { getTitle, setTitle, removeTitle } = useSessionTitles(scope)
  async function create() {
    if (creating.current) return
    creating.current = true
    const generation = ++mountedGeneration.current
    setError('')
    try {
      const session = await createSession.mutateAsync(
        createVikingBotWebSessionId(),
      )
      if (generation !== mountedGeneration.current) return
      setTitle(session.session_id, t('newChat'))
      setSelected({ id: session.session_id })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      creating.current = false
    }
  }
  function select(id: string, connection?: string) {
    mountedGeneration.current += 1
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
          <p>{capabilities.error.message}</p>
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
                disabled={createSession.isPending || health.isError}
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
              {error && (
                <p role="alert" className="p-2 text-sm text-destructive">
                  {error}
                </p>
              )}
              {activeFilter !== 'feishu' &&
                sessions.data
                  .filter((session) =>
                    isVikingBotWebSession(
                      session,
                      readPlaygroundAgentSessionIds(scope),
                    ),
                  )
                  .filter((s) =>
                    (getTitle(s.session_id) || t('newChat'))
                      .toLowerCase()
                      .includes(search.toLowerCase()),
                  )
                  .map((s) => (
                    <div
                      key={s.session_id}
                      className={`flex items-center rounded-lg hover:bg-muted ${selected?.id === s.session_id ? 'bg-muted' : ''}`}
                    >
                      <button
                        type="button"
                        className="min-w-0 flex-1 p-3 text-left text-sm"
                        onClick={() => select(s.session_id)}
                      >
                        <span className="block truncate">
                          {getTitle(s.session_id) || t('newChat')}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {t('web')}
                        </span>
                      </button>
                      <DeleteConversation
                        id={s.session_id}
                        title={getTitle(s.session_id) || t('newChat')}
                        onDeleted={() => {
                          removeTitle(s.session_id)
                          setSelected((current) =>
                            current?.id === s.session_id && !current.connection
                              ? undefined
                              : current,
                          )
                        }}
                      />
                    </div>
                  ))}
              {activeFilter !== 'web' &&
                connections.data?.map((c) => (
                  <PlatformConversationList
                    key={c.id}
                    connection={c}
                    search={search}
                    scope={scope}
                    onSelect={(connection, id) => select(id, connection)}
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
                  <Thread key={selected.id} sessionId={selected.id} />
                )
              ) : (
                <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
                  <BotIcon className="size-12 text-primary" />
                  <h2 className="text-xl font-semibold">{t('empty')}</h2>
                  <p className="max-w-md text-sm text-muted-foreground">
                    {t('emptyHint')}
                  </p>
                  <Button variant="outline" onClick={() => setTab('channels')}>
                    {t('addFeishu')}
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
