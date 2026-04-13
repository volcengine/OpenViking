import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  createSession,
  deleteSession,
  fetchSession,
  fetchSessionMessages,
  fetchSessions,
} from '../-lib/api'
import type { Message } from '../-types/message'

const SESSIONS_KEY = ['sessions'] as const

export function useSessionList() {
  return useQuery({
    queryKey: SESSIONS_KEY,
    queryFn: fetchSessions,
    staleTime: 30_000,
  })
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
  return useQuery<Message[]>({
    queryKey: [...SESSIONS_KEY, sessionId, 'messages'],
    queryFn: () => fetchSessionMessages(sessionId!),
    enabled: Boolean(sessionId),
    staleTime: 30_000, // cache for 30s to avoid flash on session switch
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
