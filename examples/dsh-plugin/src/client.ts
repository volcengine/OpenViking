/**
 * Thin OpenViking HTTP client in the memory-plugin family shape: one
 * `fetchJSON` that normalizes the `{ status, result }` envelope to
 * `{ ok, result }`, which is exactly the contract the vendored shared
 * helpers (`recall-core`, `batch-send`, `pending-queue`) consume.
 *
 * @module @openviking/dsh-plugin
 */

import type { RuntimeCfg } from './config.ts'

/** Normalized OpenViking response. */
export interface OVResponse<T = unknown> {
  ok: boolean
  result: T | null
  status?: number
  error?: { message?: string } & Record<string, unknown>
}

/** The fetchJSON contract shared helpers consume. */
export type FetchJSON = (
  path: string,
  init?: RequestInit,
  options?: { timeoutMs?: number, actorPeerId?: string },
) => Promise<OVResponse>

/** One normalized search hit. */
export interface OVSearchItem {
  uri: string
  score: number
  abstract: string
}

export class OVClient {
  readonly cfg: RuntimeCfg
  readonly fetchJSON: FetchJSON

  constructor(cfg: RuntimeCfg) {
    this.cfg = cfg
    this.fetchJSON = async (path, init, options) => {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), options?.timeoutMs ?? 10_000)
      timer.unref?.()
      try {
        const headers: Record<string, string> = { 'Content-Type': 'application/json' }
        if (this.cfg.apiKey !== '') headers.Authorization = `Bearer ${this.cfg.apiKey}`
        if (this.cfg.account !== '') headers['X-OpenViking-Account'] = this.cfg.account
        if (this.cfg.user !== '') headers['X-OpenViking-User'] = this.cfg.user
        const peer = options?.actorPeerId ?? this.cfg.peerId
        if (peer !== '') headers['X-OpenViking-Actor-Peer'] = peer
        headers['User-Agent'] = this.cfg.userAgent
        const response = await fetch(`${this.cfg.baseUrl}${path}`, {
          ...init,
          headers: { ...headers, ...init?.headers as Record<string, string> | undefined },
          signal: controller.signal,
        })
        const body = await response.json().catch(() => ({})) as {
          status?: string
          result?: unknown
          error?: { message?: string }
        }
        if (!response.ok || body.status === 'error') {
          return {
            ok: false,
            result: null,
            status: response.status,
            error: body.error ?? { message: `HTTP ${response.status}` },
          }
        }
        return { ok: true, result: body.result ?? body }
      } catch (error) {
        return { ok: false, result: null, status: 0, error: { message: error instanceof Error ? error.message : String(error) } }
      } finally {
        clearTimeout(timer)
      }
    }
  }

  async health(): Promise<boolean> {
    const response = await this.fetchJSON('/health', undefined, { timeoutMs: 5000 })
    return response.ok
  }

  /** Ensure a session exists (idempotent server-side). */
  async ensureSession(sessionId: string): Promise<boolean> {
    const response = await this.fetchJSON(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}?auto_create=true`,
      undefined,
      { timeoutMs: 5000 },
    )
    return response.ok
  }

  async commitSession(sessionId: string, keepRecentCount: number): Promise<boolean> {
    const response = await this.fetchJSON(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`,
      { method: 'POST', body: JSON.stringify({ keep_recent_count: keepRecentCount }) },
      { timeoutMs: 30_000 },
    )
    return response.ok
  }

  /** Assembled session context (used for resume seeding). */
  async getSessionContext(sessionId: string, tokenBudget: number): Promise<string> {
    const response = await this.fetchJSON(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/context?token_budget=${tokenBudget}`,
      undefined,
      { timeoutMs: 10_000 },
    )
    if (!response.ok || response.result === null || typeof response.result !== 'object') return ''
    const record = response.result as Record<string, unknown>
    const overview = record.latest_archive_overview
    return typeof overview === 'string' ? overview.trim() : ''
  }

  /** Raw semantic search across memories / resources / skills. */
  async find(query: string, limit: number): Promise<OVSearchItem[]> {
    const response = await this.fetchJSON('/api/v1/search/find', {
      method: 'POST',
      body: JSON.stringify({ query, limit }),
    })
    if (!response.ok || response.result === null || typeof response.result !== 'object') return []
    const record = response.result as Record<string, unknown>
    const items: OVSearchItem[] = []
    for (const bucket of ['memories', 'resources', 'skills']) {
      const list = record[bucket]
      if (!Array.isArray(list)) continue
      for (const raw of list) {
        if (raw === null || typeof raw !== 'object') continue
        const item = raw as Record<string, unknown>
        if (typeof item.uri !== 'string' || item.uri === '') continue
        items.push({
          uri: item.uri,
          score: typeof item.score === 'number' ? item.score : 0,
          abstract: typeof item.abstract === 'string' ? item.abstract : '',
        })
      }
    }
    items.sort((a, b) => b.score - a.score)
    return items
  }

  /** Full content read for one URI. */
  async readContent(uri: string): Promise<string> {
    const response = await this.fetchJSON(`/api/v1/content/read?uri=${encodeURIComponent(uri)}`)
    return response.ok && typeof response.result === 'string' ? response.result : ''
  }

  /** Ephemeral session + commit: the `ov add-memory` flow. */
  async addMemory(content: string): Promise<string> {
    const created = await this.fetchJSON('/api/v1/sessions', { method: 'POST', body: JSON.stringify({}) })
    const sessionId = created.ok && created.result !== null && typeof created.result === 'object'
      ? (created.result as { session_id?: unknown }).session_id
      : undefined
    if (typeof sessionId !== 'string' || sessionId === '') {
      throw new Error('OpenViking did not return a session_id')
    }
    const added = await this.fetchJSON(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/messages`,
      { method: 'POST', body: JSON.stringify({ role: 'user', content }) },
    )
    if (!added.ok) throw new Error(added.error?.message ?? 'failed to add memory message')
    const committed = await this.fetchJSON(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/commit`,
      { method: 'POST', body: JSON.stringify({}) },
      { timeoutMs: 30_000 },
    )
    if (!committed.ok) throw new Error(committed.error?.message ?? 'failed to commit memory session')
    return sessionId
  }
}
