/**
 * OpenViking memory & context plugin for deepseek-harness (dsh).
 *
 * A thin dsh adapter over the OpenViking memory-plugin family's shared
 * runtime (`shared/`, vendored from `examples/memory-plugin-shared` by its
 * `sync.mjs`): credentials, capture filtering, durable delivery with replay,
 * and recall (server-side query expansion, cross-turn dedup, peer scoping,
 * compression) are all family code. What is dsh-specific here:
 *
 * - **Capture** maps dsh `session/event` surface messages into family
 *   capture payloads; `turn/end` flushes and commits (throttled).
 * - **Recall** runs in an `agent/pre-step` waterfall and appends the shared
 *   `buildRecallBlock` output as a durable, source-attributed user message.
 *   It never touches the system prompt, so it works under every preset —
 *   including personas declared `complete: true`, where prompt-assembly
 *   contributions are silently discarded.
 * - **Seed**: on `agent/session-start` (resume) the committed OpenViking
 *   session overview is queued via `agent.inject()`.
 * - **Tools**: optional `ov_search` / `ov_read` / `ov_add_memory`.
 *
 * @module @openviking/dsh-plugin
 */

import type { Context } from '@deepseek-ai/cordis'
import type { PreStepDecision } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import type { UserMessage } from '@deepseek-ai/dsh-llm'
import { defineTool } from '@deepseek-ai/dsh-tools'
import type {} from '@deepseek-ai/dsh-session'
import { buildRecallBlock } from '../shared/recall-core.mjs'
import { OVClient } from './client.ts'
import { Config, buildRuntimeCfg } from './config.ts'
import { CaptureSync } from './sync.ts'

export type { Config } from './config.ts'
export { OVClient } from './client.ts'
export { CaptureSync } from './sync.ts'

/** Cordis plugin name used by loader diagnostics and message sources. */
export const name = 'openviking'

/** The agent registry that owns pre-step processing and session events. */
export const inject = ['agents']

const VERSION = '0.2.0'

/** Extract the plain text of one message's content blocks. */
function contentText(content: readonly unknown[] | undefined): string {
  if (!Array.isArray(content)) return ''
  return content
    .filter((block): block is { type: 'text', text: string } =>
      block !== null && typeof block === 'object'
      && (block as { type?: unknown }).type === 'text'
      && typeof (block as { text?: unknown }).text === 'string')
    .map(block => block.text)
    .join('\n')
}

/** Find the newest genuine (human-sourced) user text in a claimed batch. */
function latestUserText(messages: readonly UserMessage[]): string {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]!
    if (message.source.kind !== 'user') continue
    const text = contentText(message.content)
    if (text.trim() !== '') return text.slice(0, 2000)
  }
  return ''
}

/**
 * Register capture, recall, seeding, and tools for the lifetime of `ctx`.
 * Without a resolvable connection the plugin logs once and stays inert.
 */
export function apply(ctx: Context, config: Config): void {
  const cfg = buildRuntimeCfg(config, VERSION)
  if (cfg === undefined) {
    console.warn(
      'openviking-dsh-plugin: no server configured '
      + '(set OPENVIKING_BASE_URL / OPENVIKING_API_KEY or ~/.openviking/ovcli.conf) — plugin is inactive',
    )
    return
  }
  const client = new OVClient(cfg)
  const sync = new CaptureSync(client, config)
  sync.replayOnce()

  // ---- capture: mirror surface messages, flush + commit on turn end ----
  if (config.syncTurns !== false) {
    ctx.on('session/event', (session, event) => {
      const dshSessionId = String(session.id)
      switch (event.type) {
        case 'user/message': {
          // Only human input: plugin/tool-sourced injections (including our
          // own recall messages) must never echo back into memory.
          if (event.data.source.kind !== 'user') break
          sync.capture(dshSessionId, 'user', contentText(event.data.content))
          break
        }
        case 'assistant/message': {
          sync.capture(dshSessionId, 'assistant', contentText(event.data.message.content))
          break
        }
        case 'turn/end': {
          sync.turnEnd(dshSessionId)
          break
        }
        default:
          break
      }
    })
  }

  // ---- recall: append shared recall block at steps that claim user input ----
  if (config.autoRecall !== false) {
    ctx.on('agent/pre-step', async ({ agent, signal }, next): Promise<PreStepDecision> => {
      const decision = await next()
      if (decision.kind === 'reject' || signal.aborted) return decision
      const query = latestUserText(decision.messages)
      if (query.trim().length < (config.minQueryLength ?? 3)) return decision
      let block: string | null = null
      try {
        // Passing the OV session id turns on server-side query expansion and
        // the cross-turn dedup ledger; peer scoping rides the cfg.
        block = await buildRecallBlock(client.fetchJSON, cfg, query, {
          sessionId: CaptureSync.ovSessionId(String(agent.session.id)),
        })
      } catch {
        return decision
      }
      if (block === null || block === '') return decision
      return {
        kind: 'enter',
        messages: [
          ...decision.messages,
          createUserMessage({
            content: [{ type: 'text', text: block }],
            source: { kind: 'plugin', plugin: name, form: 'recall' },
          }),
        ],
      }
    }, { prepend: true })
  }

  // ---- seed: queue committed session overview for the first pre-step ----
  if (config.sessionSeed !== false) {
    ctx.on('agent/session-start', ({ agent, source }) => {
      if (source !== 'resume') return
      const ovId = CaptureSync.ovSessionId(String(agent.session.id))
      void client.getSessionContext(ovId, config.seedTokenBudget ?? 2000)
        .then((text) => {
          if (text === '') return
          agent.inject(createUserMessage({
            content: [{ type: 'text', text }],
            source: {
              kind: 'plugin',
              plugin: name,
              form: 'snapshot',
              sections: [{ name: 'openviking-session-context', text }],
            },
          }))
        })
        .catch(() => {})
    })
  }

  // ---- tools: explicit model-facing access (optional seam) ----
  if (config.tools !== false) {
    ctx.inject(['tools'], (child) => {
      registerTools(child, client)
    })
  }
}

/** Register the three OpenViking tools on one tools-capable context. */
function registerTools(ctx: Context, client: OVClient): void {
  ctx.tools.register(defineTool({
    name: 'ov_search',
    description:
      'Semantic search over the OpenViking context database (agent memories, indexed '
      + 'resources, and skills). Returns URIs with relevance scores and abstracts. '
      + 'Use ov_read to fetch the full content of a result.',
    parameters: {
      query: { type: 'string', required: true, description: 'What to look for, in natural language.' },
      limit: { type: 'integer', description: 'Maximum results (default 10).' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          results: {
            type: 'array',
            required: true,
            items: {
              type: 'object',
              additionalProperties: false,
              properties: {
                uri: { type: 'string', required: true },
                score: { type: 'number', required: true },
                abstract: { type: 'string' },
              },
            },
          },
        },
      },
      render: (_args, value) => [{
        type: 'text',
        text: value.results.length === 0
          ? 'No OpenViking results.'
          : value.results.map(item =>
            `${item.uri} (${item.score.toFixed(2)})${item.abstract ? ` — ${item.abstract}` : ''}`,
          ).join('\n'),
      }],
    },
    async execute(args) {
      const items = await client.find(args.query, args.limit ?? 10)
      return {
        results: items.map(item => ({
          uri: item.uri,
          score: item.score,
          ...item.abstract === '' ? {} : { abstract: item.abstract.slice(0, 500) },
        })),
      }
    },
    presentCall: args => ({ card: 'generic', title: 'OpenViking search', kind: 'read', rawInput: args.query }),
  }))

  ctx.tools.register(defineTool({
    name: 'ov_read',
    description: 'Read the full text content of one OpenViking URI (viking://...) returned by ov_search.',
    parameters: {
      uri: { type: 'string', required: true, description: 'The viking:// URI to read.' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          uri: { type: 'string', required: true },
          content: { type: 'string', required: true },
        },
      },
      render: (_args, value) => [{ type: 'text', text: value.content }],
    },
    async execute(args) {
      if (!args.uri.startsWith('viking://')) throw new Error('ov_read requires a viking:// URI')
      const content = await client.readContent(args.uri)
      return { uri: args.uri, content: content.length > 16_000 ? `${content.slice(0, 16_000)}…` : content }
    },
    presentCall: args => ({ card: 'generic', title: 'OpenViking read', kind: 'read', rawInput: args.uri }),
  }))

  ctx.tools.register(defineTool({
    name: 'ov_add_memory',
    description:
      'Store one durable memory in OpenViking (facts, decisions, preferences worth '
      + 'remembering across sessions). Write the memory as one self-contained statement.',
    parameters: {
      content: { type: 'string', required: true, description: 'The memory text to store.' },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          stored: { type: 'boolean', required: true },
          sessionId: { type: 'string', required: true },
        },
      },
      render: (_args, value) => [{
        type: 'text',
        text: value.stored ? 'Memory stored in OpenViking.' : 'Memory storage failed.',
      }],
    },
    async execute(args) {
      const sessionId = await client.addMemory(args.content)
      return { stored: true, sessionId }
    },
    presentCall: args => ({ card: 'generic', title: 'OpenViking add memory', kind: 'other', rawInput: args.content }),
  }))
}
