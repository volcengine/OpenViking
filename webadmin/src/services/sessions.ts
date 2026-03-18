import apiClient, { handleAPI, APIResponse } from './api'

// Session types
export interface Message {
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp?: string
}

// Backend session response format
export interface BackendSession {
  session_id: string
  uri: string
  is_dir: boolean
}

// Frontend normalized session format
export interface Session {
  id: string
  messages: Message[]
  created_at: string
  updated_at: string
  compressed?: boolean
  memory_extracted?: boolean
}

export interface SessionStats {
  total_sessions: number
  active_sessions: number
  compressed_sessions: number
}

// Normalize backend session format to frontend format
const normalizeSession = (backend: BackendSession): Session => ({
  id: backend.session_id,
  messages: [],
  created_at: backend.uri,
  updated_at: backend.uri,
  compressed: !backend.is_dir  // is_dir: true means not compressed
})

// Session service
export const sessionService = {
  /**
   * Create a new session
   */
  create: async (): Promise<APIResponse<Session>> => {
    return handleAPI<BackendSession>(
      apiClient.post('/sessions')
    ).then(res => ({
      ...res,
      data: res.data ? normalizeSession(res.data) : undefined
    }))
  },

  /**
   * List all sessions
   */
  list: async (): Promise<APIResponse<Session[]>> => {
    return handleAPI<BackendSession[]>(
      apiClient.get('/sessions')
    ).then(res => ({
      ...res,
      data: res.data ? res.data.map(normalizeSession) : undefined
    }))
  },

  /**
   * Get a specific session
   */
  get: async (session_id: string): Promise<APIResponse<Session>> => {
    return handleAPI<BackendSession>(
      apiClient.get(`/sessions/${session_id}`)
    ).then(res => ({
      ...res,
      data: res.data ? normalizeSession(res.data) : undefined
    }))
  },

  /**
   * Add a message to session
   */
  addMessage: async (
    session_id: string,
    role: 'user' | 'assistant' | 'system',
    content: string
  ): Promise<APIResponse<Message>> => {
    return handleAPI<Message>(
      apiClient.post(`/sessions/${session_id}/messages`, { role, content })
    )
  },

  /**
   * Commit session (extract memories)
   */
  commit: async (
    session_id: string,
    wait: boolean = true
  ): Promise<APIResponse<Session>> => {
    return handleAPI<BackendSession>(
      apiClient.post(`/sessions/${session_id}/commit`, { wait })
    ).then(res => ({
      ...res,
      data: res.data ? normalizeSession(res.data) : undefined
    }))
  },

  /**
   * Delete a session
   */
  delete: async (session_id: string): Promise<APIResponse<void>> => {
    return handleAPI<void>(
      apiClient.delete(`/sessions/${session_id}`)
    )
  },

  /**
   * Get session statistics
   */
  getStats: async (): Promise<APIResponse<SessionStats>> => {
    const sessions = await sessionService.list()
    if (!sessions.success || !sessions.data) {
      return {
        success: true,
        data: {
          total_sessions: 0,
          active_sessions: 0,
          compressed_sessions: 0
        }
      }
    }

    return {
      success: true,
      data: {
        total_sessions: sessions.data.length,
        active_sessions: sessions.data.filter(s => !s.compressed).length,
        compressed_sessions: sessions.data.filter(s => s.compressed).length
      }
    }
  },

  /**
   * Compress a session
   */
  compress: async (session_id: string): Promise<APIResponse<void>> => {
    return handleAPI<void>(
      apiClient.post('', {
        method: 'POST',
        path: `/api/v1/sessions/${session_id}/compress`
      })
    )
  },

  /**
   * Export session data
   */
  export: async (session_id: string): Promise<APIResponse<Session>> => {
    return sessionService.get(session_id)
  }
}

export default sessionService
