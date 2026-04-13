import { useMemo, useCallback } from 'react'
import type { ExternalStoreThreadListAdapter } from '@assistant-ui/react'

import { useSessionList, useCreateSession, useDeleteSession } from './use-sessions'
import { useSessionTitles, removeSessionTitle, setSessionTitle } from './use-session-titles'

export function useThreadListAdapter(
  activeSessionId: string | null,
  setActiveSessionId: (id: string | null) => void,
  searchQuery?: string,
): ExternalStoreThreadListAdapter {
  const { data: sessions, isLoading } = useSessionList()
  const createSession = useCreateSession()
  const deleteSession = useDeleteSession()
  const { getTitle } = useSessionTitles()

  const threads = useMemo(() => {
    const all = (sessions ?? [])
      .map((s) => ({
        id: s.session_id,
        remoteId: s.session_id,
        status: 'regular' as const,
        title: getTitle(s.session_id),
      }))
      .reverse()

    if (!searchQuery?.trim()) return all

    const q = searchQuery.trim().toLowerCase()
    return all.filter(
      (t) =>
        t.title.toLowerCase().includes(q) ||
        t.id.toLowerCase().includes(q),
    )
  }, [sessions, getTitle, searchQuery])

  const onSwitchToNewThread = useCallback(async () => {
    const result = await createSession.mutateAsync(undefined)
    setSessionTitle(result.session_id, '新会话')
    setActiveSessionId(result.session_id)
  }, [createSession, setActiveSessionId])

  const onSwitchToThread = useCallback(
    (threadId: string) => {
      setActiveSessionId(threadId)
    },
    [setActiveSessionId],
  )

  const onDelete = useCallback(
    async (threadId: string) => {
      await deleteSession.mutateAsync(threadId)
      removeSessionTitle(threadId)
      if (activeSessionId === threadId) {
        setActiveSessionId(null)
      }
    },
    [deleteSession, activeSessionId, setActiveSessionId],
  )

  return useMemo(
    () => ({
      threadId: activeSessionId ?? undefined,
      isLoading,
      threads,
      archivedThreads: [],
      onSwitchToNewThread,
      onSwitchToThread,
      onDelete,
    }),
    [activeSessionId, isLoading, threads, onSwitchToNewThread, onSwitchToThread, onDelete],
  )
}
