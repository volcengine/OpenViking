/**
 * Capture side: mirrors dsh conversation turns into OpenViking sessions
 * through the shared family runtime — `capture-utils` filters and bounds
 * text, `batch-send` delivers with automatic spill into the durable
 * `pending-queue` (replayed on the next start), and commits are throttled
 * per session.
 *
 * Ordering: appends join a per-session promise chain, so a slow batch never
 * reorders mirrored history. Every entry point is failure-contained.
 *
 * @module @openviking/dsh-plugin
 */

import { sendSessionMessages } from '../shared/batch-send.mjs'
import { sanitizeCapturedText, shouldCaptureText, truncateCaptureText } from '../shared/capture-utils.mjs'
import { replayPending } from '../shared/pending-queue.mjs'
import { deriveHarnessSessionId } from '../shared/session-model.mjs'
import type { OVClient } from './client.ts'
import type { Config } from './config.ts'

/** One mirrored message payload in the family wire shape. */
interface CapturePayload {
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

/** Report one degraded operation per kind per minute, not a log storm. */
class RateLimitedReporter {
  private readonly last = new Map<string, number>()

  report(kind: string, error: unknown): void {
    const now = Date.now()
    const previous = this.last.get(kind)
    if (previous !== undefined && now - previous < 60_000) return
    this.last.set(kind, now)
    const message = error instanceof Error ? error.message : String(error)
    console.warn(`openviking-dsh-plugin: ${kind} degraded — ${message}`)
  }
}

export class CaptureSync {
  private readonly client: OVClient
  private readonly config: Config
  private readonly ensured = new Set<string>()
  private readonly pending = new Map<string, CapturePayload[]>()
  private readonly chains = new Map<string, Promise<void>>()
  private readonly flushTimers = new Map<string, NodeJS.Timeout>()
  private readonly lastCommit = new Map<string, number>()
  private readonly reporter = new RateLimitedReporter()
  private replayed = false

  constructor(client: OVClient, config: Config) {
    this.client = client
    this.config = config
  }

  /** Map a dsh session id onto its mirrored OpenViking session id. */
  static ovSessionId(dshSessionId: string): string {
    return deriveHarnessSessionId('dsh-', dshSessionId)
  }

  /** Queue one mirrored message; flushes are batched and ordered. */
  capture(dshSessionId: string, role: 'user' | 'assistant', rawText: string): void {
    const sanitized = sanitizeCapturedText(rawText)
    const decision = shouldCaptureText(sanitized, role, this.config as Record<string, unknown>)
    if (!decision.shouldCapture) return
    const text = truncateCaptureText(decision.text)
    const queue = this.pending.get(dshSessionId) ?? []
    queue.push({ role, content: text, created_at: new Date().toISOString() })
    this.pending.set(dshSessionId, queue)
    this.scheduleFlush(dshSessionId, queue.length >= 20 ? 0 : 250)
  }

  /** Flush queued messages now and commit when the throttle allows. */
  turnEnd(dshSessionId: string): void {
    this.chain(dshSessionId, async () => {
      await this.flushNow(dshSessionId)
      if (this.config.commitOnTurnEnd === false) return
      const now = Date.now()
      const previous = this.lastCommit.get(dshSessionId)
      if (previous !== undefined && now - previous < (this.config.commitMinIntervalMs ?? 300_000)) return
      this.lastCommit.set(dshSessionId, now)
      const committed = await this.client.commitSession(
        CaptureSync.ovSessionId(dshSessionId),
        this.config.commitKeepRecentCount ?? 10,
      )
      if (!committed) this.reporter.report('commit', 'commit request failed')
    })
  }

  /** Replay messages a previous process failed to deliver. Runs once. */
  replayOnce(): void {
    if (this.replayed) return
    this.replayed = true
    void replayPending(this.client.fetchJSON, () => {}).catch((error: unknown) => {
      this.reporter.report('replay', error)
    })
  }

  /** Resolve when every queued message and commit has settled. */
  async quiesce(): Promise<void> {
    for (const timer of this.flushTimers.values()) clearTimeout(timer)
    this.flushTimers.clear()
    const sessions = [...new Set([...this.pending.keys(), ...this.chains.keys()])]
    for (const id of sessions) this.chain(id, () => this.flushNow(id))
    await Promise.all([...this.chains.values()])
  }

  private scheduleFlush(dshSessionId: string, delayMs: number): void {
    if (delayMs === 0) {
      const timer = this.flushTimers.get(dshSessionId)
      if (timer !== undefined) {
        clearTimeout(timer)
        this.flushTimers.delete(dshSessionId)
      }
      this.chain(dshSessionId, () => this.flushNow(dshSessionId))
      return
    }
    if (this.flushTimers.has(dshSessionId)) return
    const timer = setTimeout(() => {
      this.flushTimers.delete(dshSessionId)
      this.chain(dshSessionId, () => this.flushNow(dshSessionId))
    }, delayMs)
    timer.unref?.()
    this.flushTimers.set(dshSessionId, timer)
  }

  private async flushNow(dshSessionId: string): Promise<void> {
    const queue = this.pending.get(dshSessionId)
    if (queue === undefined || queue.length === 0) return
    this.pending.set(dshSessionId, [])
    const ovId = CaptureSync.ovSessionId(dshSessionId)
    try {
      if (!this.ensured.has(dshSessionId)) {
        if (await this.client.ensureSession(ovId)) this.ensured.add(dshSessionId)
      }
      // Delivery failures spill into the durable pending queue and replay
      // on the next start — shared behaviour, not plugin logic.
      const result = await sendSessionMessages(this.client.fetchJSON, ovId, queue)
      if (result.failed > 0 || result.enqueueFailed > 0) {
        this.reporter.report('capture', result.lastError ?? 'send failed')
      }
    } catch (error) {
      this.reporter.report('capture', error)
    }
  }

  /** Serialize work per session so mirrored history keeps its order. */
  private chain(dshSessionId: string, task: () => Promise<void>): void {
    const previous = this.chains.get(dshSessionId) ?? Promise.resolve()
    const next = previous.then(task, task)
    this.chains.set(dshSessionId, next)
  }
}
