import { useEffect, useRef, useState } from 'react'
import { createFileRoute } from '@tanstack/react-router'
import { AssistantRuntimeProvider } from '@assistant-ui/react'
import { CompassIcon } from 'lucide-react'

import { Thread } from '#/components/assistant-ui/thread'
import { ThreadList } from '#/components/assistant-ui/thread-list'
import { useAssistantRuntime } from './-hooks/use-assistant-runtime'
import { useCreateSession } from './-hooks/use-sessions'
import { setSessionTitle } from './-hooks/use-session-titles'

export const Route = createFileRoute('/sessions/')({
  component: SessionsPage,
})

function SessionsPage() {
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const searchInputRef = useRef<HTMLInputElement>(null)
  const runtime = useAssistantRuntime(activeSessionId, setActiveSessionId, searchQuery)
  const createSession = useCreateSession()

  // Cmd+N to create new session, Cmd+K to focus search
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey)) return

      if (e.key === 'n') {
        e.preventDefault()
        createSession.mutateAsync(undefined).then((result) => {
          setSessionTitle(result.session_id, '新会话')
          setActiveSessionId(result.session_id)
        })
      } else if (e.key === 'k') {
        e.preventDefault()
        searchInputRef.current?.focus()
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [createSession, setActiveSessionId])

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div className="-mx-4 -my-6 md:-mx-6 flex h-[calc(100svh-3rem)]">
        <div className="w-[260px] shrink-0 border-r border-border bg-sidebar">
          <ThreadList
            searchQuery={searchQuery}
            onSearchChange={setSearchQuery}
            searchInputRef={searchInputRef}
          />
        </div>
        <div className="flex-1 min-w-0 bg-background">
          {activeSessionId ? (
            <Thread sessionId={activeSessionId} />
          ) : (
            <SessionsEmpty />
          )}
        </div>
      </div>
    </AssistantRuntimeProvider>
  )
}

function SessionsEmpty() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-6">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-muted">
        <CompassIcon className="size-7 text-muted-foreground" />
      </div>
      <div className="text-center">
        <h3 className="text-sm font-medium text-foreground">No session selected</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          Select a session from the sidebar, or create a new one.
        </p>
      </div>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px]">⌘</kbd>
        <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px]">N</kbd>
        <span>new session</span>
        <span className="mx-1 text-border">·</span>
        <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px]">⌘</kbd>
        <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[11px]">K</kbd>
        <span>search</span>
      </div>
    </div>
  )
}
