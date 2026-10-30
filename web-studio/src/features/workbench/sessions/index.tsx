import { getRouteApi, Link, useNavigate } from '@tanstack/react-router'
import { ArrowLeftIcon, CompassIcon } from 'lucide-react'
import { useCallback, useEffect } from 'react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { Thread } from '#/features/workbench/sessions/components/thread'
import { ThreadList } from '#/features/workbench/sessions/components/thread-list'
import { useSessionTitles } from '#/features/workbench/sessions/lib/use-session-titles'
import { useCreateSession } from '#/features/workbench/sessions/lib/use-sessions'
import { cn } from '#/shared/lib/utils'
import { Button } from '#/shared/ui/button'

const routeApi = getRouteApi('/sessions/')

const COMMAND_KEY_LABEL = '⌘'
const NEW_SESSION_KEY_LABEL = 'N'

export function SessionsPage() {
  const { t } = useTranslation('sessions')
  const { provider } = useStudioService()
  const { s: activeSessionId } = routeApi.useSearch()
  const { identityScopeKey } = useAppConnection()
  const navigate = useNavigate()
  const createSession = useCreateSession()
  const { setTitle } = useSessionTitles(identityScopeKey)

  const handleNewSession = useCallback(async () => {
    const result = await createSession.mutateAsync(undefined)
    setTitle(result.session_id, t('threadList.newSession'))
    void navigate({ to: '/sessions', search: { s: result.session_id } })
  }, [createSession, navigate, setTitle, t])

  // Cmd+N to create new session
  useEffect(() => {
    if (provider !== 'opensource') return
    const handler = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return
      if (e.key === 'n') {
        e.preventDefault()
        handleNewSession()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [handleNewSession, provider])

  return (
    <div className="@container/sessions -mx-4 -my-6 flex h-[calc(100svh-3rem)] min-w-0 overflow-hidden md:-mx-6">
      <ThreadList
        activeSessionId={activeSessionId}
        className={
          activeSessionId
            ? 'hidden @min-[40rem]/sessions:flex'
            : 'w-full @min-[40rem]/sessions:w-72'
        }
      />
      <section
        className={cn(
          'min-w-0 flex-1 flex-col bg-background',
          activeSessionId ? 'flex' : 'hidden @min-[40rem]/sessions:flex',
        )}
      >
        {activeSessionId ? (
          <>
            <div className="shrink-0 border-b border-border/70 px-4 py-2 @min-[40rem]/sessions:hidden">
              <Button
                render={<Link to="/sessions" search={{ s: undefined }} />}
                nativeButton={false}
                variant="ghost"
                size="sm"
              >
                <ArrowLeftIcon />
                {t('threadList.backToList')}
              </Button>
            </div>
            <div className="min-h-0 flex-1">
              <Thread sessionId={activeSessionId} />
            </div>
          </>
        ) : (
          <SessionsEmpty />
        )}
      </section>
    </div>
  )
}

function SessionsEmpty() {
  const { t } = useTranslation('sessions')
  const { provider } = useStudioService()

  return (
    <div className="flex h-full flex-col items-center justify-center gap-6">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-muted">
        <CompassIcon className="size-7 text-muted-foreground" />
      </div>
      <div className="text-center">
        <h3 className="text-sm font-medium text-foreground">
          {t('empty.title')}
        </h3>
        <p className="mt-1 text-sm text-muted-foreground">
          {provider === 'opensource'
            ? t('empty.description')
            : t('studio:selectSessionRecord')}
        </p>
      </div>
      {provider === 'opensource' ? (
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px]">
            {COMMAND_KEY_LABEL}
          </kbd>
          <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px]">
            {NEW_SESSION_KEY_LABEL}
          </kbd>
          <span>{t('threadList.newSession')}</span>
        </div>
      ) : null}
    </div>
  )
}
