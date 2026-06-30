import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo } from 'react'

import {
  createSession,
  deleteSession,
  fetchBotHealth,
  fetchSession,
  fetchSessionMessages,
  fetchSessions,
} from './api'
import { useAppConnection } from '#/hooks/use-app-connection'
import {
  getSessionMessagesQueryKey,
  getSessionQueryKey,
  getSessionScopeKey,
  getSessionsQueryKey,
} from './query-keys'
import type { Message } from './types/message'
import type { CreateSessionResult, SessionListItem } from './types/session'

const BOT_HEALTH_KEY = ['bot', 'health'] as const

function useSessionScope() {
  const { connection, connectionRole } = useAppConnection()
  return useMemo(
    () => getSessionScopeKey(connection, connectionRole),
    [
      connection.accountId,
      connection.adminApiKey,
      connection.apiKey,
      connection.baseUrl,
      connection.userId,
      connectionRole,
    ],
  )
}

function appendSessionToList(
  sessions: SessionListItem[] | undefined,
  result: CreateSessionResult,
): SessionListItem[] {
  const nextSession = {
    is_dir: true,
    session_id: result.session_id,
    uri: result.uri,
  } satisfies SessionListItem
  const existing = sessions ?? []
  if (existing.some((session) => session.session_id === result.session_id)) {
    return existing
  }
  return [...existing, nextSession]
}

export function useBotHealth() {
  return useQuery({
    queryKey: BOT_HEALTH_KEY,
    queryFn: fetchBotHealth,
    retry: false,
    staleTime: 15_000,
  })
}

export function useSessionList() {
  const scope = useSessionScope()

  return useQuery({
    queryKey: getSessionsQueryKey(scope),
    queryFn: fetchSessions,
    staleTime: 30_000,
  })
}

export function useSession(sessionId: string | undefined) {
  const scope = useSessionScope()

  return useQuery({
    queryKey: getSessionQueryKey(scope, sessionId),
    queryFn: () => fetchSession(sessionId!),
    enabled: Boolean(sessionId),
    staleTime: 15_000,
  })
}

/** Fetch message history for a session. */
export function useSessionMessages(sessionId: string | undefined) {
  const scope = useSessionScope()

  return useQuery<Message[]>({
    queryKey: sessionId
      ? getSessionMessagesQueryKey(scope, sessionId)
      : getSessionQueryKey(scope, sessionId),
    queryFn: () => fetchSessionMessages(sessionId!),
    enabled: Boolean(sessionId),
    staleTime: 30_000, // cache for 30s to avoid flash on session switch
  })
}

export function useCreateSession() {
  const queryClient = useQueryClient()
  const scope = useSessionScope()
  const sessionsKey = getSessionsQueryKey(scope)

  return useMutation({
    mutationFn: (sessionId?: string) => createSession(sessionId),
    onSuccess: (result) => {
      queryClient.setQueryData<SessionListItem[]>(sessionsKey, (sessions) =>
        appendSessionToList(sessions, result),
      )
      void queryClient.invalidateQueries({ queryKey: sessionsKey })
    },
  })
}

export function useDeleteSession() {
  const queryClient = useQueryClient()
  const scope = useSessionScope()

  return useMutation({
    mutationFn: (sessionId: string) => deleteSession(sessionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: getSessionsQueryKey(scope),
      })
    },
  })
}
