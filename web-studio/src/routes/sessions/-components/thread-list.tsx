import { useCallback } from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import {
  LoaderCircleIcon,
  MessageSquareIcon,
  PlusIcon,
  Trash2Icon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import { useAppConnection } from '#/hooks/use-app-connection'
import {
  useCreateSession,
  useDeleteSession,
  useSessionListByRecency,
} from '#/lib/sessions/use-sessions'
import { useSessionTitles } from '#/lib/sessions/use-session-titles'
import { cn } from '#/lib/utils'

interface ThreadListProps {
  activeSessionId?: string
}

export function ThreadList({ activeSessionId }: ThreadListProps) {
  const { i18n, t } = useTranslation('sessions')
  const { identityScopeKey } = useAppConnection()
  const navigate = useNavigate()
  const { data: sessions, isLoading } = useSessionListByRecency()
  const { getTitle, removeTitle, setTitle } = useSessionTitles(identityScopeKey)
  const createSession = useCreateSession()
  const deleteSession = useDeleteSession()

  const handleNewSession = useCallback(async () => {
    const result = await createSession.mutateAsync(undefined)
    setTitle(result.session_id, t('threadList.newSession'))
    void navigate({ to: '/sessions', search: { s: result.session_id } })
  }, [createSession, navigate, setTitle, t])

  const handleDeleteSession = useCallback(
    async (event: React.MouseEvent, sessionId: string) => {
      event.preventDefault()
      event.stopPropagation()
      await deleteSession.mutateAsync(sessionId)
      removeTitle(sessionId)

      if (activeSessionId === sessionId) {
        const nextSession = sessions.find(
          (session) => session.session_id !== sessionId,
        )
        void navigate({
          to: '/sessions',
          search: { s: nextSession?.session_id },
        })
      }
    },
    [activeSessionId, deleteSession, navigate, removeTitle, sessions],
  )

  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-border/70 bg-muted/20">
      <div className="flex h-16 shrink-0 items-center justify-between border-b border-border/70 px-4">
        <div className="min-w-0">
          <h1 className="text-sm font-semibold text-foreground">
            {t('threadList.title')}
          </h1>
          {!isLoading ? (
            <p className="mt-0.5 text-xs text-muted-foreground">
              {t('threadList.count', { count: sessions.length })}
            </p>
          ) : null}
        </div>
        <Button
          type="button"
          size="icon-sm"
          variant="outline"
          disabled={createSession.isPending}
          onClick={handleNewSession}
          aria-label={t('threadList.newSession')}
          title={t('threadList.newSession')}
        >
          {createSession.isPending ? (
            <LoaderCircleIcon className="animate-spin" />
          ) : (
            <PlusIcon />
          )}
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {isLoading ? (
          <div className="flex h-28 items-center justify-center gap-2 text-sm text-muted-foreground">
            <LoaderCircleIcon className="size-4 animate-spin" />
            <span>{t('threadList.loading')}</span>
          </div>
        ) : sessions.length === 0 ? (
          <div className="flex h-40 flex-col items-center justify-center px-6 text-center">
            <div className="flex size-9 items-center justify-center rounded-xl bg-muted">
              <MessageSquareIcon className="size-4 text-muted-foreground" />
            </div>
            <p className="mt-3 text-sm font-medium text-foreground">
              {t('threadList.emptyTitle')}
            </p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {t('threadList.emptyDescription')}
            </p>
          </div>
        ) : (
          <div className="space-y-1">
            {sessions.map((session) => {
              const isActive = activeSessionId === session.session_id
              const title = getTitle(session.session_id)

              return (
                <div
                  key={session.session_id}
                  className={cn(
                    'group/session relative rounded-xl transition-colors',
                    isActive
                      ? 'bg-accent text-accent-foreground shadow-sm ring-1 ring-border/60'
                      : 'hover:bg-muted/80',
                  )}
                >
                  <Link
                    to="/sessions"
                    search={{ s: session.session_id }}
                    className="flex min-w-0 items-start gap-2.5 px-3 py-2.5 pr-9 outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <MessageSquareIcon
                      className={cn(
                        'mt-0.5 size-4 shrink-0',
                        isActive ? 'text-primary' : 'text-muted-foreground',
                      )}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium">
                        {title}
                      </span>
                      <span className="mt-1 block truncate font-mono text-[11px] text-muted-foreground">
                        {formatSessionTime(
                          session.mod_time,
                          i18n.resolvedLanguage,
                        )}
                      </span>
                    </span>
                  </Link>
                  <button
                    type="button"
                    onClick={(event) =>
                      void handleDeleteSession(event, session.session_id)
                    }
                    disabled={deleteSession.isPending}
                    className="absolute right-2 top-2.5 flex size-6 items-center justify-center rounded-md text-muted-foreground opacity-0 transition-[opacity,color,background-color] hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring group-hover/session:opacity-100"
                    aria-label={t('threadList.deleteSession', { title })}
                    title={t('threadList.deleteSession', { title })}
                  >
                    <Trash2Icon className="size-3.5" />
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="shrink-0 border-t border-border/70 px-4 py-3 text-[11px] text-muted-foreground">
        {t('threadList.shortcut')}
      </div>
    </aside>
  )
}

function formatSessionTime(value: string, locale?: string): string {
  if (!value) return ''

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''

  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'short',
    timeStyle: 'short',
  }).format(date)
}
