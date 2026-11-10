import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { readFilesystemAgentSessionIds } from '#/features/workbench/filesystem/lib/utils'
import { DeleteConversation } from '#/features/workbench/sessions/components/delete-conversation'
import { Thread } from '#/features/workbench/sessions/components/thread'
import { useDefaultConversationTitles } from '#/features/workbench/sessions/lib/use-default-conversation-titles'
import { useSessionTitles } from '#/features/workbench/sessions/lib/use-session-titles'
import {
  useBotHealth,
  useSessionListByRecency,
} from '#/features/workbench/sessions/lib/use-sessions'
import {
  createVikingBotWebSessionId,
  isVikingBotWebSession,
} from '#/features/workbench/sessions/lib/vikingbot-sessions'
import { ConversationRow } from '#/shared/components/conversation-row'
import { Button } from '#/shared/ui/button'
import { Input } from '#/shared/ui/input'
import {
  ArrowLeftIcon,
  BotIcon,
  MessageSquareIcon,
  PlusIcon,
} from 'lucide-react'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

export function VikingBotPage() {
  const { identityScopeKey } = useAppConnection()
  return <VikingBotWorkspace key={identityScopeKey} scope={identityScopeKey} />
}

function VikingBotWorkspace({ scope }: { scope: string }) {
  const { t } = useTranslation('vikingbot')
  const [selected, setSelected] = useState<{
    id: string
    draft?: boolean
  }>()
  const [search, setSearch] = useState('')
  const health = useBotHealth()
  const sessions = useSessionListByRecency()
  const { getTitle, removeTitle } = useSessionTitles(scope)
  useDefaultConversationTitles(
    scope,
    sessions.data
      .filter((session) =>
        isVikingBotWebSession(session, readFilesystemAgentSessionIds(scope)),
      )
      .map((session) => session.session_id),
  )
  const rows = [
    ...sessions.data
      .filter((session) =>
        isVikingBotWebSession(session, readFilesystemAgentSessionIds(scope)),
      )
      .map((session) => ({
        id: session.session_id,
        title:
          getTitle(session.session_id) === session.session_id
            ? t('newChat')
            : getTitle(session.session_id),
        time: session.mod_time,
      })),
  ]
    .filter((row) => row.title.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => (Date.parse(b.time) || 0) - (Date.parse(a.time) || 0))
  function create() {
    setSelected({ id: createVikingBotWebSessionId(), draft: true })
  }
  function select(id: string) {
    setSelected({ id })
  }
  return (
    <div className="-mx-4 -my-6 flex h-[calc(100svh-3rem)] min-w-0 flex-col overflow-hidden md:-mx-6">
      <header className="flex shrink-0 items-center gap-4 border-b px-4 py-4 md:px-6">
        <h1 className="flex items-center gap-2 font-semibold">
          <BotIcon className="size-5 text-primary" />
          {t('title')}
        </h1>
      </header>
      {health.isError && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 border-b p-3 text-sm text-destructive"
        >
          {t('workbenchUnavailable')}
          <Button
            variant="outline"
            size="sm"
            onClick={() => void health.refetch()}
          >
            {t('retry')}
          </Button>
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <aside
          className={`${selected ? 'hidden md:flex' : 'flex'} w-full shrink-0 flex-col border-r md:w-72`}
        >
          <div className="space-y-3 border-b p-3">
            <Button
              className="w-full"
              disabled={health.isError || health.isPending}
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
          </div>
          <div className="flex-1 overflow-auto p-2">
            {rows.map((row) => (
              <ConversationRow
                key={row.id}
                title={row.title}
                subtitle={t('web')}
                time={row.time}
                icon={<MessageSquareIcon className="size-4" />}
                selected={selected?.id === row.id}
                onSelect={() => select(row.id)}
                action={
                  <DeleteConversation
                    id={row.id}
                    title={row.title}
                    onDeleted={() => {
                      removeTitle(row.id)
                      setSelected((current) =>
                        current?.id === row.id ? undefined : current,
                      )
                    }}
                  />
                }
              />
            ))}
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
          <div className="min-h-0 flex-1">
            {selected ? (
              <Thread
                key={selected.id}
                sessionId={selected.id}
                draft={selected.draft}
                onPersisted={() =>
                  setSelected((current) =>
                    current?.id === selected.id
                      ? { ...current, draft: false }
                      : current,
                  )
                }
              />
            ) : (
              <div className="flex h-full flex-col items-center justify-center gap-4 p-8 text-center">
                <BotIcon className="size-12 text-primary" />
                <h2 className="text-xl font-semibold">{t('empty')}</h2>
                <p className="max-w-md text-sm text-muted-foreground">
                  {t('emptyHint')}
                </p>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  )
}
