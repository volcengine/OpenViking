import { useCallback, useEffect } from 'react'
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { CompassIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { useCreateSession } from '#/lib/sessions/use-sessions'
import { setSessionTitle } from '#/lib/sessions/use-session-titles'
import { Thread } from './-components/thread'

const COMMAND_KEY_LABEL = '⌘'
const NEW_SESSION_KEY_LABEL = 'N'

export const Route = createFileRoute('/sessions/')({
  component: SessionsPage,
  validateSearch: (search: Record<string, unknown>) =>
    ({
      s: (search.s as string) || undefined,
    }) as { s?: string },
})

function SessionsPage() {
  const { t } = useTranslation('sessions')
  const { s: activeSessionId } = Route.useSearch()
  const navigate = useNavigate()
  const createSession = useCreateSession()

  const handleNewSession = useCallback(async () => {
    const result = await createSession.mutateAsync(undefined)
    setSessionTitle(result.session_id, t('threadList.newSession'))
    void navigate({ to: '/sessions', search: { s: result.session_id } })
  }, [createSession, navigate, t])

  // Cmd+N to create new session
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return
      if (e.key === 'n') {
        e.preventDefault()
        handleNewSession()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [handleNewSession])

  return (
    <div className="-mx-4 -my-6 md:-mx-6 flex h-[calc(100svh-3rem)]">
      <div className="flex-1 min-w-0 bg-background">
        {activeSessionId ? (
          <Thread sessionId={activeSessionId} />
        ) : (
          <SessionsEmpty />
        )}
      </div>
    </div>
  )
}

function SessionsEmpty() {
  const { t } = useTranslation('sessions')

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
          {t('empty.description')}
        </p>
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px]">
          {COMMAND_KEY_LABEL}
        </kbd>
        <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px]">
          {NEW_SESSION_KEY_LABEL}
        </kbd>
        <span>{t('threadList.newSession')}</span>
      </div>
    </div>
  )
}
