import {
  deleteSessionBySessionId,
  getContentRead,
  getSessionIdArchiveByArchiveId,
  getSessions,
  getSessionBySessionId,
  getSessionIdContext,
  postBotV1Chat,
  postSessions,
  postSessionIdCommit,
  postSessionIdExtract,
  postSessionIdMessages,
  postSessionIdUsed,
} from '#/gen/ov-client/sdk.gen'
import {
  getOvResult,
  normalizeOvClientError,
  OvClientError,
  ovClient,
} from '#/lib/ov-client'

import { parseSessionMemoryDiff } from './memory-diff'
import type { BotChatRequest, BotChatResponse } from '@ov-server/bot/v1/chat'
import type { SessionMemoryDiff } from './memory-diff'
import type { Message, MessagePart } from './types/message'
import type {
  AddMessageResult,
  CommitSessionResult,
  CreateSessionResult,
  DeleteSessionResult,
  SessionArchiveResult,
  SessionContextResult,
  SessionListItem,
  SessionMeta,
} from '@ov-server/api/v1/sessions'
import type { UsedRequest } from '#/gen/ov-client/types.gen'

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

export async function createSession(
  sessionId?: string,
): Promise<CreateSessionResult> {
  return getOvResult<CreateSessionResult>(
    postSessions({
      body: sessionId ? { session_id: sessionId } : undefined,
    }),
  )
}

export async function fetchSessionContext(
  sessionId: string,
  tokenBudget?: number,
): Promise<SessionContextResult> {
  return getOvResult<SessionContextResult>(
    getSessionIdContext({
      path: { session_id: sessionId },
      query:
        tokenBudget === undefined ? undefined : { token_budget: tokenBudget },
    }),
  )
}

export async function fetchSessionArchive(
  sessionId: string,
  archiveId: string,
): Promise<SessionArchiveResult> {
  return getOvResult<SessionArchiveResult>(
    getSessionIdArchiveByArchiveId({
      path: { archive_id: archiveId, session_id: sessionId },
    }),
  )
}

export async function deleteSession(
  sessionId: string,
): Promise<DeleteSessionResult> {
  return getOvResult<DeleteSessionResult>(
    deleteSessionBySessionId({
      path: { session_id: sessionId },
    }),
  )
}

// ---------------------------------------------------------------------------
// Session Messages
// ---------------------------------------------------------------------------

function isMessage(value: unknown): value is Message {
  return (
    typeof value === 'object' &&
    value !== null &&
    'id' in value &&
    'role' in value &&
    'parts' in value
  )
}

function getMessages(value: unknown): Message[] {
  return Array.isArray(value) ? value.filter(isMessage) : []
}

function deduplicateMessages(messages: Message[]): Message[] {
  const seen = new Set<string>()
  return messages.filter((message) => {
    if (seen.has(message.id)) return false
    seen.add(message.id)
    return true
  })
}

const SESSION_ARCHIVE_CONCURRENCY = 4

async function mapWithConcurrency<T, TResult>(
  items: T[],
  concurrency: number,
  mapper: (item: T, index: number) => Promise<TResult>,
): Promise<TResult[]> {
  const results = new Array<TResult>(items.length)
  let nextIndex = 0

  async function worker() {
    while (nextIndex < items.length) {
      const index = nextIndex
      nextIndex += 1
      results[index] = await mapper(items[index], index)
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(concurrency, items.length) }, () => worker()),
  )
  return results
}

function isMissingArchive(error: unknown): boolean {
  const normalized = normalizeOvClientError(error)
  return normalized.statusCode === 404 || normalized.code === 'NOT_FOUND'
}

/**
 * Fetch the complete message history.
 *
 * `/context` only contains messages after the latest completed archive, so
 * archived messages must be loaded separately and prepended in archive order.
 */
export async function fetchSessionMessages(
  sessionId: string,
  sessionMeta?: SessionMeta,
): Promise<Message[]> {
  const context = await getOvResult<SessionContextResult>(
    getSessionIdContext({
      path: { session_id: sessionId },
    }),
  )

  let commitCount = 0
  try {
    const session = sessionMeta ?? (await fetchSession(sessionId))
    commitCount = Math.max(0, Math.floor(session.commit_count || 0))
  } catch {
    // Older servers may not expose session details. Current context is still
    // useful, so preserve the previous behavior as a fallback.
  }

  const archiveIds = Array.from(
    { length: commitCount },
    (_, index) => `archive_${String(index + 1).padStart(3, '0')}`,
  )
  const archives = await mapWithConcurrency(
    archiveIds,
    SESSION_ARCHIVE_CONCURRENCY,
    async (archiveId) => {
      try {
        return await fetchSessionArchive(sessionId, archiveId)
      } catch (error) {
        if (isMissingArchive(error)) return null
        throw error
      }
    },
  )
  const archivedMessages = archives.flatMap((archive) =>
    archive ? getMessages(archive.messages) : [],
  )

  return deduplicateMessages([
    ...archivedMessages,
    ...getMessages(context.messages),
  ])
}

export async function fetchSessionMemoryDiffs(
  session: SessionMeta,
): Promise<SessionMemoryDiff[]> {
  const commitCount = Math.max(0, Math.floor(session.commit_count || 0))
  if (commitCount === 0) return []

  const sessionUri =
    session.uri?.replace(/\/+$/, '') ||
    `viking://user/${session.user.user_id}/sessions/${session.session_id}`
  const archiveIds = Array.from(
    { length: commitCount },
    (_, index) => `archive_${String(index + 1).padStart(3, '0')}`,
  )
  const results = await mapWithConcurrency(
    archiveIds,
    SESSION_ARCHIVE_CONCURRENCY,
    async (archiveId) => {
      try {
        const result = await getOvResult<unknown>(
          getContentRead({
            query: {
              limit: -1,
              offset: 0,
              raw: true,
              uri: `${sessionUri}/history/${archiveId}/memory_diff.json`,
            } as Parameters<typeof getContentRead>[0]['query'] & {
              raw?: boolean
            },
          }),
        )
        return parseSessionMemoryDiff(result, archiveId)
      } catch (error) {
        if (isMissingArchive(error)) return null
        throw error
      }
    },
  )

  return results
    .flatMap((result) => (result ? [result] : []))
    .sort((left, right) => right.archiveId.localeCompare(left.archiveId))
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

export async function commitSession(
  sessionId: string,
  keepRecentCount?: number,
): Promise<CommitSessionResult> {
  return getOvResult<CommitSessionResult>(
    postSessionIdCommit({
      body:
        keepRecentCount === undefined
          ? undefined
          : { keep_recent_count: keepRecentCount },
      path: { session_id: sessionId },
    }),
  )
}

export async function extractSession(sessionId: string): Promise<unknown> {
  return getOvResult<unknown>(
    postSessionIdExtract({
      path: { session_id: sessionId },
    }),
  )
}

export async function recordSessionUsed(
  sessionId: string,
  body: UsedRequest,
): Promise<unknown> {
  return getOvResult<unknown>(
    postSessionIdUsed({
      body,
      path: { session_id: sessionId },
    }),
  )
}

export async function fetchSessionToolResults(
  sessionId: string,
  options: { limit?: number; toolName?: string } = {},
): Promise<unknown> {
  const response = await ovClient.instance.get(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}/tool-results`,
    {
      params: {
        limit: options.limit,
        tool_name: options.toolName || undefined,
      },
    },
  )
  return getOvResult<unknown>(Promise.resolve(response))
}

export async function fetchSessionToolResult(
  sessionId: string,
  toolResultId: string,
  options: { includeMetadata?: boolean; limit?: number; offset?: number } = {},
): Promise<unknown> {
  const response = await ovClient.instance.get(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}/tool-results/${encodeURIComponent(
      toolResultId,
    )}`,
    {
      params: {
        include_metadata: options.includeMetadata,
        limit: options.limit,
        offset: options.offset,
      },
    },
  )
  return getOvResult<unknown>(Promise.resolve(response))
}

export async function searchSessionToolResult(
  sessionId: string,
  toolResultId: string,
  query: string,
  options: { contextChars?: number; limit?: number } = {},
): Promise<unknown> {
  const response = await ovClient.instance.get(
    `/api/v1/sessions/${encodeURIComponent(sessionId)}/tool-results/${encodeURIComponent(
      toolResultId,
    )}/search`,
    {
      params: {
        context_chars: options.contextChars,
        limit: options.limit,
        q: query,
      },
    },
  )
  return getOvResult<unknown>(Promise.resolve(response))
}

// ---------------------------------------------------------------------------
// Bot Chat
// ---------------------------------------------------------------------------

function extractErrorMessage(text: string, fallback: string): string {
  if (!text.trim()) return fallback

  try {
    const parsed = JSON.parse(text) as unknown
    if (parsed && typeof parsed === 'object') {
      const record = parsed as Record<string, unknown>
      if (typeof record.detail === 'string') return record.detail
      const error = record.error
      if (error && typeof error === 'object') {
        const message = (error as Record<string, unknown>).message
        if (typeof message === 'string') return message
      }
    }
  } catch {
    // Fall through to raw text.
  }

  return text
}

function buildFetchHeaders(): Record<string, string> {
  const conn = ovClient.getConnection()
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (conn.apiKey) headers['X-API-Key'] = conn.apiKey
  if (conn.identityHeaders) {
    if (conn.accountId) headers['X-OpenViking-Account'] = conn.accountId
    if (conn.userId) headers['X-OpenViking-User'] = conn.userId
  }
  return headers
}

export async function fetchBotHealth(): Promise<unknown> {
  const baseUrl = ovClient.getOptions().baseUrl
  const response = await fetch(`${baseUrl}/bot/v1/health`, {
    method: 'GET',
    headers: buildFetchHeaders(),
  })

  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new OvClientError({
      code: response.status === 503 ? 'BOT_MODE_DISABLED' : 'BOT_HEALTH_FAILED',
      message: extractErrorMessage(
        text,
        `Bot health check failed (${response.status})`,
      ),
      responseBody: text,
      statusCode: response.status,
    })
  }

  return response.json().catch(() => ({ status: 'ok' }))
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
  const conn = ovClient.getConnection()
  const response = await fetch(`${baseUrl}/bot/v1/chat/stream`, {
    method: 'POST',
    headers: buildFetchHeaders(),
    body: JSON.stringify({
      ...request,
      user_id: request.user_id || conn.userId || undefined,
      stream: true,
    }),
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
export async function sendChat(
  request: BotChatRequest,
): Promise<BotChatResponse> {
  const conn = ovClient.getConnection()
  const response = await postBotV1Chat({
    body: {
      ...request,
      user_id: request.user_id || conn.userId || undefined,
    },
    throwOnError: true,
  } as unknown as NonNullable<Parameters<typeof postBotV1Chat<true>>[0]>)

  return response.data as BotChatResponse
}

// ---------------------------------------------------------------------------
// Part serialization helpers (Message → API request format)
// ---------------------------------------------------------------------------

export function serializeParts(
  parts: MessagePart[],
): Array<Record<string, unknown>> {
  return parts.map((part) => {
    if (part.type === 'text') {
      return { type: 'text', text: part.text }
    }
    if (part.type === 'context') {
      return {
        type: 'context',
        uri: part.uri,
        context_type: part.context_type,
        abstract: part.abstract,
      }
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
    if (part.completion_tokens != null)
      d.completion_tokens = part.completion_tokens
    return d
  })
}
