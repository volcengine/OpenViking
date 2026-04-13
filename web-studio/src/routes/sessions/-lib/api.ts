import {
  deleteSessionBySessionId,
  getSessions,
  getSessionBySessionId,
  getSessionIdContext,
  postSessions,
  postSessionIdCommit,
  postSessionIdMessages,
} from '#/gen/ov-client/sdk.gen'
import { getOvResult, normalizeOvClientError, ovClient } from '#/lib/ov-client'

import type { BotChatRequest, BotChatResponse } from '../-types/chat'
import type { Message, MessagePart } from '../-types/message'
import type {
  AddMessageResult,
  CommitSessionResult,
  CreateSessionResult,
  DeleteSessionResult,
  SessionListItem,
  SessionMeta,
} from '../-types/session'

// ---------------------------------------------------------------------------
// Session CRUD
// ---------------------------------------------------------------------------

export async function fetchSessions(): Promise<SessionListItem[]> {
  const result = await getOvResult<SessionListItem[]>(getSessions())
  return Array.isArray(result) ? result : []
}

export async function fetchSession(sessionId: string): Promise<SessionMeta> {
  return getOvResult<SessionMeta>(
    getSessionBySessionId({
      path: { session_id: sessionId },
    }),
  )
}

export async function createSession(sessionId?: string): Promise<CreateSessionResult> {
  return getOvResult<CreateSessionResult>(
    postSessions({
      body: sessionId ? { session_id: sessionId } : undefined,
    }),
  )
}

export async function deleteSession(sessionId: string): Promise<DeleteSessionResult> {
  return getOvResult<DeleteSessionResult>(
    deleteSessionBySessionId({
      path: { session_id: sessionId },
    }),
  )
}

// ---------------------------------------------------------------------------
// Session Messages
// ---------------------------------------------------------------------------

/** Fetch message history for a session via the /context endpoint. */
export async function fetchSessionMessages(sessionId: string): Promise<Message[]> {
  const result = await getOvResult<{ messages?: unknown[] }>(
    getSessionIdContext({
      path: { session_id: sessionId },
    }),
  )
  const raw = result?.messages
  if (!Array.isArray(raw)) return []
  // Each item is Message.to_dict() — { id, role, parts, created_at }
  return raw.filter(
    (m): m is Message =>
      typeof m === 'object' && m !== null && 'id' in m && 'role' in m && 'parts' in m,
  )
}

export async function addMessage(
  sessionId: string,
  role: 'user' | 'assistant',
  content?: string,
  parts?: Array<Record<string, unknown>>,
): Promise<AddMessageResult> {
  return getOvResult<AddMessageResult>(
    postSessionIdMessages({
      path: { session_id: sessionId },
      body: {
        role,
        content: parts ? undefined : content,
        parts: parts ?? undefined,
      },
    }),
  )
}

export async function commitSession(sessionId: string): Promise<CommitSessionResult> {
  return getOvResult<CommitSessionResult>(
    postSessionIdCommit({
      path: { session_id: sessionId },
    }),
  )
}

// ---------------------------------------------------------------------------
// Bot Chat
// ---------------------------------------------------------------------------

function buildFetchHeaders(): Record<string, string> {
  const conn = ovClient.getConnection()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (conn.apiKey) headers['X-API-Key'] = conn.apiKey
  if (conn.accountId) headers['X-OpenViking-Account'] = conn.accountId
  if (conn.userId) headers['X-OpenViking-User'] = conn.userId
  headers['X-OpenViking-Agent'] = 'web-studio'
  return headers
}

/**
 * Send a streaming chat request. Returns the raw Response for SSE parsing.
 * Use parseSseStream() from ./sse.ts to iterate over events.
 */
export async function sendChatStream(
  request: BotChatRequest,
  signal?: AbortSignal,
): Promise<Response> {
  const baseUrl = ovClient.getOptions().baseUrl
  const response = await fetch(`${baseUrl}/bot/v1/chat/stream`, {
    method: 'POST',
    headers: buildFetchHeaders(),
    body: JSON.stringify({ ...request, stream: true }),
    signal,
  })

  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw normalizeOvClientError(
      new Error(`Chat stream request failed (${response.status}): ${text}`),
    )
  }

  return response
}

/** Send a non-streaming chat request. */
export async function sendChat(request: BotChatRequest): Promise<BotChatResponse> {
  const baseUrl = ovClient.getOptions().baseUrl
  const response = await fetch(`${baseUrl}/bot/v1/chat`, {
    method: 'POST',
    headers: buildFetchHeaders(),
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw normalizeOvClientError(
      new Error(`Chat request failed (${response.status}): ${text}`),
    )
  }

  return response.json() as Promise<BotChatResponse>
}

// ---------------------------------------------------------------------------
// Part serialization helpers (Message → API request format)
// ---------------------------------------------------------------------------

export function serializeParts(parts: MessagePart[]): Array<Record<string, unknown>> {
  return parts.map((part) => {
    if (part.type === 'text') {
      return { type: 'text', text: part.text }
    }
    if (part.type === 'context') {
      return { type: 'context', uri: part.uri, context_type: part.context_type, abstract: part.abstract }
    }
    // tool
    const d: Record<string, unknown> = {
      type: 'tool',
      tool_id: part.tool_id,
      tool_name: part.tool_name,
      tool_uri: part.tool_uri,
      skill_uri: part.skill_uri,
      tool_status: part.tool_status,
    }
    if (part.tool_input) d.tool_input = part.tool_input
    if (part.tool_output) d.tool_output = part.tool_output
    if (part.duration_ms != null) d.duration_ms = part.duration_ms
    if (part.prompt_tokens != null) d.prompt_tokens = part.prompt_tokens
    if (part.completion_tokens != null) d.completion_tokens = part.completion_tokens
    return d
  })
}
