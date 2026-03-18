import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { sessionService } from '../services/sessions'

// Query keys
export const SESSIONS_QUERY_KEY = ['sessions']
export const SESSION_QUERY_KEY = (id: string) => [...SESSIONS_QUERY_KEY, id]

// List sessions hook
export const useSessions = () => {
  return useQuery({
    queryKey: SESSIONS_QUERY_KEY,
    queryFn: sessionService.list,
    refetchInterval: 30000
  })
}

// Get session hook
export const useSession = (id: string) => {
  return useQuery({
    queryKey: SESSION_QUERY_KEY(id),
    queryFn: () => sessionService.get(id),
    enabled: !!id
  })
}

// Create session mutation
export const useCreateSession = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: sessionService.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY })
    }
  })
}

// Add message mutation
export const useAddMessage = (sessionId: string) => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ role, content }: { role: 'user' | 'system' | 'assistant'; content: string }) =>
      sessionService.addMessage(sessionId, role, content),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: SESSION_QUERY_KEY(sessionId) })
      queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY })
    }
  })
}

// Commit session mutation
export const useCommitSession = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ sessionId, wait }: { sessionId: string; wait: boolean }) =>
      sessionService.commit(sessionId, wait),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: SESSION_QUERY_KEY(variables.sessionId) })
      queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY })
    }
  })
}

// Delete session mutation
export const useDeleteSession = () => {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: sessionService.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY })
    }
  })
}

// Session stats hook
export const useSessionStats = () => {
  return useQuery({
    queryKey: [...SESSIONS_QUERY_KEY, 'stats'],
    queryFn: sessionService.getStats
  })
}

export default useSessions
