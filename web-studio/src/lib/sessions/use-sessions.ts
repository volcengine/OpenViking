import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  createSession,
  deleteSession,
  fetchBotHealth,
  fetchSession,
  fetchSessionMemoryDiffs,
  fetchSessionMessages,
  fetchSessions,
} from './api'
import type { Message } from './types/message'
import type { SessionMemoryDiff } from './memory-diff'
import type { SessionListItem, SessionMeta } from '@ov-server/api/v1/sessions'

const SESSIONS_KEY = ['sessions'] as const
const BOT_HEALTH_KEY = ['bot', 'health'] as const

export function useBotHealth() {
  return useQuery({
    queryKey: BOT_HEALTH_KEY,
    queryFn: fetchBotHealth,
    retry: false,
    staleTime: 15_000,
  })
}

export function useSessionList() {
  return useQuery({
    queryKey: SESSIONS_KEY,
    queryFn: fetchSessions,
    staleTime: 30_000,
  })
}

/**
 * Session list ordered by recency (newest first).
 *
 * The list API requests recent sessions before applying its storage limit.
 * We keep a client-side sort for deterministic display and put sessions
 * without a timestamp at the bottom.
 */
export function useSessionListByRecency() {
  const { data: sessions, isLoading } = useSessionList()

  if (!sessions) return { data: [] as SessionListItem[], isLoading }

  const data = [...sessions].sort((a, b) => {
    const aTime = a.mod_time || ''
    const bTime = b.mod_time || ''
    // Missing timestamps sort to bottom.
    if (aTime === '' && bTime === '') return 0
    if (aTime === '') return 1
    if (bTime === '') return -1
    // ISO-8601 UTC strings: lexicographic compare == chronological.
    return bTime.localeCompare(aTime)
  })

  return { data, isLoading }
}

export function useSession(sessionId: string | undefined) {
  return useQuery({
    queryKey: [...SESSIONS_KEY, sessionId],
    queryFn: () => fetchSession(sessionId!),
    enabled: Boolean(sessionId),
    staleTime: 15_000,
  })
}

/** Fetch message history for a session. */
export function useSessionMessages(sessionId: string | undefined) {
  const queryClient = useQueryClient()

  return useQuery<Message[]>({
    queryKey: [...SESSIONS_KEY, sessionId, 'messages'],
    queryFn: async () => {
      const session = await queryClient.fetchQuery({
        queryFn: () => fetchSession(sessionId!),
        queryKey: [...SESSIONS_KEY, sessionId],
        staleTime: 15_000,
      })
      return fetchSessionMessages(sessionId!, session)
    },
    enabled: Boolean(sessionId),
    staleTime: 30_000, // cache for 30s to avoid flash on session switch
  })
}

export function useSessionMemoryDiffs(
  session: SessionMeta | undefined,
  enabled = true,
) {
  return useQuery<SessionMemoryDiff[]>({
    queryKey: [
      ...SESSIONS_KEY,
      session?.session_id,
      'memory-diffs',
      session?.commit_count,
      session?.last_commit_at,
    ],
    queryFn: () => fetchSessionMemoryDiffs(session!),
    enabled: Boolean(enabled && session && session.commit_count > 0),
    staleTime: 30_000,
  })
}

export function useCreateSession() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (sessionId?: string) => createSession(sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: SESSIONS_KEY })
    },
  })
}

export function useDeleteSession() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (sessionId: string) => deleteSession(sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: SESSIONS_KEY })
    },
  })
}
