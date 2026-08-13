/**
 * Plugin configuration.
 *
 * Behaviour knobs live in cordis config (validated by schemastery) and use
 * the memory-plugin family's canonical names. Credentials are NOT plugin
 * config: they resolve through the shared `credentials.mjs` chain
 * (`OPENVIKING_*` env → `~/.openviking/ovcli.conf` → `~/.openviking/ov.conf`),
 * identical to every other OpenViking memory plugin.
 *
 * @module @openviking/dsh-plugin
 */

import z from '@deepseek-ai/schemastery'
import { resolveOpenVikingCredentials } from '../shared/credentials.mjs'
import { resolveEffectivePeerId } from '../shared/workspace-peer.mjs'

/** Behaviour switches; every knob has a working default. */
export interface Config {
  /** Override the resolved server base URL (testing / special deployments). */
  baseUrl?: string
  /** Mirror user/assistant messages into an OpenViking session. */
  syncTurns?: boolean
  /** Inject recalled OpenViking context at steps that claim user input. */
  autoRecall?: boolean
  /** Maximum recalled items per injection. */
  recallLimit?: number
  /** Minimum relevance score for recalled items. */
  scoreThreshold?: number
  /** Shortest query worth a recall round-trip. */
  minQueryLength?: number
  /** Token budget for one recall block. */
  recallTokenBudget?: number
  /** Prefer stored abstracts over full content reads during recall. */
  recallPreferAbstract?: boolean
  /** Seed a resumed session with committed OpenViking session context. */
  sessionSeed?: boolean
  /** Token budget for the session seed context. */
  seedTokenBudget?: number
  /** Commit the OpenViking session (memory extraction) after turns end. */
  commitOnTurnEnd?: boolean
  /** Minimum milliseconds between commits. 0 commits at every turn end. */
  commitMinIntervalMs?: number
  /** Messages preserved verbatim across a commit. */
  commitKeepRecentCount?: number
  /** Derive an actor peer from the workspace path when none is configured. */
  workspacePeer?: boolean
  /** Register the ov_search / ov_read / ov_add_memory model-facing tools. */
  tools?: boolean
}

/** Schemastery validation for {@link Config}. */
export const Config: z<Config> = z.object({
  baseUrl: z.string(),
  syncTurns: z.boolean().default(true),
  autoRecall: z.boolean().default(true),
  recallLimit: z.number().default(5),
  scoreThreshold: z.number().default(0.35),
  minQueryLength: z.number().default(3),
  recallTokenBudget: z.number().default(2000),
  recallPreferAbstract: z.boolean().default(true),
  sessionSeed: z.boolean().default(true),
  seedTokenBudget: z.number().default(2000),
  commitOnTurnEnd: z.boolean().default(true),
  commitMinIntervalMs: z.number().default(300_000),
  commitKeepRecentCount: z.number().default(10),
  workspacePeer: z.boolean().default(true),
  tools: z.boolean().default(true),
})

/** Resolved connection identity plus the flat cfg the shared helpers read. */
export interface RuntimeCfg {
  baseUrl: string
  apiKey: string
  account: string
  user: string
  peerId: string
  userAgent: string
  /** Flat knob view consumed by shared recall-core / capture-utils. */
  [key: string]: unknown
}

/**
 * Merge shared-chain credentials, the workspace peer, and plugin knobs into
 * the flat cfg object every shared helper consumes.
 * @returns the runtime cfg, or `undefined` when no server is configured.
 */
export function buildRuntimeCfg(config: Config, version: string): RuntimeCfg | undefined {
  const credentials = resolveOpenVikingCredentials(process.env)
  const baseUrl = config.baseUrl !== undefined && config.baseUrl !== ''
    ? config.baseUrl.replace(/\/+$/, '')
    : credentials.baseUrl
  if (baseUrl === undefined || baseUrl === '') return undefined
  const { peerId } = resolveEffectivePeerId({
    cfg: { peerId: credentials.peerId, workspacePeer: config.workspacePeer !== false },
    cwd: process.cwd(),
  })
  return {
    ...config,
    baseUrl,
    apiKey: credentials.apiKey,
    account: credentials.account,
    user: credentials.user,
    peerId,
    userAgent: `openviking-dsh-plugin/${version}`,
  }
}
