import { createAdminClient } from '#/lib/admin'
import type { AdminConnection } from '#/lib/admin'
import { getOvResult, isOvClientError } from '#/lib/ov-client'

/** Wire protocol an upstream speaks; the gateway never converts between them. */
export type Protocol = 'anthropic' | 'chat' | 'responses'
export type Vendor =
  | 'generic'
  | 'anthropic'
  | 'openai'
  | 'deepseek'
  | 'ark'
  | 'byteplus'
export type AuthMode = 'managed' | 'passthrough'

/** Sources recall searches, in display order. */
export const CONTEXT_TYPES = ['memory', 'resource', 'skill'] as const
export type ContextType = (typeof CONTEXT_TYPES)[number]

/** Categories for "Limit by category", in display order. */
export const QUOTA_BUCKETS = [
  'events',
  'entities',
  'preferences',
  'experiences',
  'resources',
  'skills',
] as const
export type QuotaBucket = (typeof QUOTA_BUCKETS)[number]

/** A tool from OpenViking MCP, using its original name. */
export type GatewayTool = {
  name: string
  description: string
  annotations?: { readOnlyHint?: boolean } & Record<string, unknown>
}

/** Request types in the log, in the order the type filter lists them. */
export const LOG_KINDS = [
  'user',
  'continuation',
  'auxiliary',
  'subagent',
  'count',
  'passthrough',
  'capture',
] as const
export type LogKind = (typeof LOG_KINDS)[number]

export type CaptureStatus = 'active' | 'disabled' | 'retrying' | 'paused'

type UpstreamSettings = {
  name: string
  protocol: Protocol
  base_url: string
  auth_mode: AuthMode
  models: string[]
  /** Name clients use → model sent to the upstream. */
  aliases: Record<string, string>
  priority: number
  enabled: boolean
  vendor: Vendor
  allow_gateway_tools: boolean
  /**
   * Send back the reasoning clients drop from relayed replies; null follows
   * the vendor (see `replaysReasoning`).
   */
  replay_reasoning: boolean | null
  coding_plan: boolean
  allow_coding_plan: boolean
  cache_min_tokens: number
  /** Upstream model name → context window in tokens. */
  context_windows: Record<string, number>
}

/** An upstream as listed by the gateway; secrets are never returned. */
export type Upstream = UpstreamSettings & {
  id: string
  revision: number
  has_api_key: boolean
  header_names: string[]
}

/**
 * Body of `PUT upstreams/{id}`. A blank `api_key` keeps the stored key; a
 * header with a blank value keeps its stored value; omitted headers are removed.
 */
export type UpstreamInput = UpstreamSettings & {
  api_key: string
  headers: Record<string, string>
}

/** Every field of a context profile (the gateway's `Policy`). */
export type ProfileSettings = {
  name: string
  recall: boolean
  profile: boolean
  profile_max_tokens: number
  capture: boolean
  context_types: ContextType[]
  quotas: Partial<Record<QuotaBucket, number>>
  max_tokens: number
  session_max_tokens: number
  score_threshold: number
  recall_timeout: number
  query_max_chars: number
  commit_tokens: number
  keep_recent_messages: number
  idle_seconds: number
  compaction: boolean
  compaction_threshold: number
  summary_max_tokens: number
  context_window: number | null
  agent_windows: boolean
  window_soft_ratio: number
  window_hard_ratio: number
  gateway_tools: boolean
  show_tool_calls: boolean
  disabled_tools: string[]
  tool_max_rounds: number
  tool_timeout_seconds: number
  tool_result_bytes: number
  tool_total_seconds: number
  tool_total_tokens: number
}

export type Profile = ProfileSettings & { id: string; revision: number }

/** A gateway key as listed; the secret itself is only returned once. */
export type GatewayKey = {
  id: string
  revision: number
  name: string
  policy_id: string
  upstream_ids: string[]
  models: string[]
  user_id: string
  /** First 12 characters of the secret, e.g. `ovcg_Ab3dE9x`. */
  prefix: string
  /** Epoch seconds. */
  created_at: number
}

export type IssuedKey = GatewayKey & { key: string }

/** Gateway keys that use an upstream or a context profile; undefined while keys are unknown. */
export function keysUsing(
  keys: GatewayKey[] | undefined,
  target: { upstreamId: string } | { profileId: string },
): number | undefined {
  return keys?.filter((key) =>
    'upstreamId' in target
      ? key.upstream_ids.includes(target.upstreamId)
      : key.policy_id === target.profileId,
  ).length
}

/**
 * Who a new key acts as: a user of this account, whose key OpenViking Server
 * looks up itself, or that user's OpenViking key pasted by the admin.
 */
export type KeyOwner =
  | { user_id: string; openviking_key?: never }
  | { openviking_key: string; user_id?: never }

export type KeyRequest = KeyOwner & {
  name: string
  policy_id: string
  upstream_ids: string[]
  models: string[]
}

/** A user of this account a key can act as; root is never one of them. */
export type KeyUser = {
  user_id: string
  role: 'admin' | 'user'
  /** False when the server can't read the user's key, e.g. it keeps only a hash. */
  api_key_available: boolean
}

export type CacheStats = {
  requests: number
  input_tokens: number
  cached_tokens: number
  cache_hit_ratio: number
}

/** Last OpenViking health check of the gateway process that answered. */
export type OpenVikingHealth = {
  status: string
  healthy?: boolean
  version?: string
  auth_mode?: string
  reason?: string
}

export type Overview = {
  /** Model requests in the sample (memory-sync events excluded). */
  requests: number
  /** Epoch seconds of the newest model request, or null. */
  last_request_at: number | null
  output_tokens: number
  cache: { first_call: CacheStats; continuation: CacheStats }
  degradations: Record<string, number>
  openviking: OpenVikingHealth
  recall_count: number
  /** Requests that made a recall decision. */
  recall_requests: number
  /** Average recall time in ms over `recall_requests`. */
  recall_ms: number
  /** Conversations whose latest saving status is retrying or paused. */
  capture_issues: { retrying: number; paused: number }
  sample_limit: number
  /** Days the request log keeps records. */
  log_retention_days: number
}

/** One request-log record (metadata only); every field but `time` is optional. */
export type LogRecord = {
  /** Epoch seconds. */
  time: number
  request_id?: string
  kind?: LogKind
  model?: string
  status?: number
  upstream_id?: string
  session?: string
  protocol?: Protocol
  credential_id?: string
  replay_hits?: number
  recall_count?: number
  recall_ms?: number
  recall_reason?: string
  capture_status?: CaptureStatus
  capture_reason?: string
  /** Epoch seconds or null. */
  capture_retry_at?: number | null
  tool_skip_reason?: string
  tool_stop_reason?: string
  hidden_rounds?: number
  hidden_upstream_calls?: number
  hidden_added_tokens?: number
  first_upstream_input_tokens?: number
  first_upstream_output_tokens?: number
  first_upstream_cached_tokens?: number
  first_upstream_cache_write_tokens?: number
  hidden_upstream_input_tokens?: number
  hidden_upstream_output_tokens?: number
  hidden_upstream_cached_tokens?: number
  hidden_upstream_cache_write_tokens?: number
  degradation?: string
  /** Anchor of the cut whose replacement this request used. */
  compaction_applied?: string
  /** Why generating a summary failed; the full history was sent. */
  compaction_failed?: string
  /** Estimated size of the summary written for this request. */
  compaction_tokens?: number
  compaction_ms?: number
  /** Estimated context size and the window it was compared with. */
  context_tokens?: number
  context_window?: number
  /** Current context window, while the model manages its own windows. */
  window?: number
  window_reset?: boolean
  window_reminder?: 'soft' | 'hard'
  input_tokens?: number
  output_tokens?: number
  cached_tokens?: number
  cache_write_tokens?: number
  cache_min_tokens?: number
  cache_eligible?: boolean
  duration_ms?: number
}

/** Where clients reach the gateway. */
export type ConnectionInfo = {
  base_url: string
  public_url_configured: boolean
}

export type UpstreamTestResult = {
  ok: boolean
  status?: number
  reason?: string
}

/** Conversation to resync, taken from a log record. */
export type ResyncTarget = { session: string; protocol: Protocol }

export type GatewayErrorReason =
  | 'not_enabled'
  | 'token_missing'
  | 'token_mismatch'
  | 'unreachable'
  | 'unsupported'
  | 'conflict'
  | 'invalid'
  | 'forbidden'
  | 'not_found'
  | 'unauthorized'
  | 'other'

/** Reasons the layout answers with a setup card instead of the gateway pages. */
const UNAVAILABLE: GatewayErrorReason[] = [
  'not_enabled',
  'token_missing',
  'token_mismatch',
  'unreachable',
  'unsupported',
]

/** A failed gateway call: HTTP status, the server's own message, and a category. */
export class GatewayError extends Error {
  readonly status?: number
  readonly detail: string
  readonly reason: GatewayErrorReason

  constructor(
    detail: string,
    status?: number,
    cause?: unknown,
    reason?: GatewayErrorReason,
  ) {
    super(detail, cause ? { cause } : undefined)
    this.name = 'GatewayError'
    this.detail = detail
    this.status = status
    this.reason = reason ?? classify(detail, status)
  }

  /** True when the gateway itself is off, misconfigured, down or missing. */
  get unavailable(): boolean {
    return UNAVAILABLE.includes(this.reason)
  }
}

const AVAILABILITY: Array<[RegExp, GatewayErrorReason]> = [
  [/context gateway is not enabled/i, 'not_enabled'],
  [/management token is not configured/i, 'token_missing'],
  // The gateway's answer when OpenViking sends a different management token.
  [/invalid gateway management credential/i, 'token_mismatch'],
  [/management service is unavailable/i, 'unreachable'],
]

const STATUS_REASONS: Partial<Record<number, GatewayErrorReason>> = {
  400: 'invalid',
  401: 'unauthorized',
  403: 'forbidden',
  404: 'not_found',
  409: 'conflict',
  422: 'invalid',
}

function classify(detail: string, status?: number): GatewayErrorReason {
  const match = AVAILABILITY.find(([pattern]) => pattern.test(detail))
  if (match) return match[1]
  return (status === undefined ? undefined : STATUS_REASONS[status]) ?? 'other'
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** Reads the message out of FastAPI, gateway and OpenViking error bodies. */
function readDetail(body: unknown): string | undefined {
  if (!isRecord(body)) return typeof body === 'string' ? body : undefined
  const { detail, error } = body
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (isRecord(item) ? item.msg : item))
      .filter((item): item is string => typeof item === 'string')
    if (messages.length) return messages.join('; ')
  }
  if (isRecord(error) && typeof error.message === 'string') {
    return error.message
  }
  return undefined
}

/** Normalizes anything a gateway call can throw into a `GatewayError`. */
export function toGatewayError(error: unknown): GatewayError {
  if (error instanceof GatewayError) return error
  if (isOvClientError(error)) {
    return new GatewayError(
      readDetail(error.responseBody) ?? error.message,
      error.statusCode,
      error,
    )
  }
  return new GatewayError(
    error instanceof Error ? error.message : String(error),
    undefined,
    error,
  )
}

const BASE_PATH = '/api/v1/admin/context-gateway'

type Method = 'GET' | 'POST' | 'PUT' | 'DELETE'

type RequestOptions = { query?: Record<string, unknown>; body?: unknown }

/** One OpenViking Server call; any failure becomes a `GatewayError`. */
async function call<T>(
  connection: AdminConnection,
  method: Method,
  url: string,
  options: RequestOptions = {},
): Promise<T> {
  const body =
    options.body === undefined
      ? {}
      : {
          body: options.body,
          headers: { 'Content-Type': 'application/json' },
        }
  try {
    return await getOvResult<T>(
      createAdminClient(connection).request({
        method,
        url,
        query: options.query,
        ...body,
      }),
    )
  } catch (error) {
    throw toGatewayError(error)
  }
}

/** Calls the gateway's management API through OpenViking Server. */
function send<T>(
  connection: AdminConnection,
  method: Method,
  segments: string[],
  options?: RequestOptions,
): Promise<T> {
  const url = [BASE_PATH, ...segments.map(encodeURIComponent)].join('/')
  return call<T>(connection, method, url, options)
}

/**
 * Gateway address for clients; also the layout's availability probe. A 404
 * means the OpenViking server has no Context Gateway management at all.
 */
export async function getConnectionInfo(
  connection: AdminConnection,
): Promise<ConnectionInfo> {
  try {
    return await send<ConnectionInfo>(connection, 'GET', ['guides'])
  } catch (error) {
    const failure = toGatewayError(error)
    if (failure.status !== 404) throw failure
    throw new GatewayError(failure.detail, 404, failure, 'unsupported')
  }
}

export const getOverview = (connection: AdminConnection) =>
  send<Overview>(connection, 'GET', ['overview'])

/** Newest-first request log; the gateway caps `limit` at 1,000. */
export const listLogs = (connection: AdminConnection, limit = 200) =>
  send<LogRecord[]>(connection, 'GET', ['logs'], { query: { limit } })

export const listUpstreams = (connection: AdminConnection) =>
  send<Upstream[]>(connection, 'GET', ['upstreams'])

/** Available MCP tools; empty until this account has a gateway key. */
export const listTools = (connection: AdminConnection) =>
  send<GatewayTool[]>(connection, 'GET', ['tools'])

/** Creates or replaces an upstream. Build the body with `toUpstreamInput`. */
export const saveUpstream = (
  connection: AdminConnection,
  id: string,
  input: UpstreamInput,
) => send<Upstream>(connection, 'PUT', ['upstreams', id], { body: input })

/** Fails with reason `conflict` while keys still use the upstream. */
export const deleteUpstream = (connection: AdminConnection, id: string) =>
  send<{ deleted: boolean }>(connection, 'DELETE', ['upstreams', id])

/** Calls the upstream's model list with its stored credentials. */
export const testUpstream = (connection: AdminConnection, id: string) =>
  send<UpstreamTestResult>(connection, 'POST', ['upstreams', id, 'test'])

export const listProfiles = (connection: AdminConnection) =>
  send<Profile[]>(connection, 'GET', ['policies'])

/** Creates or replaces a profile. Build the body with `toProfileSettings`. */
export const saveProfile = (
  connection: AdminConnection,
  id: string,
  settings: ProfileSettings,
) => send<Profile>(connection, 'PUT', ['policies', id], { body: settings })

/** Fails with reason `conflict` while keys still use the profile. */
export const deleteProfile = (connection: AdminConnection, id: string) =>
  send<{ deleted: boolean }>(connection, 'DELETE', ['policies', id])

export const listKeys = (connection: AdminConnection) =>
  send<GatewayKey[]>(connection, 'GET', ['keys'])

const KEY_USER_ROLES: readonly string[] = ['admin', 'user']

/**
 * Users of the current account a key can act as. The server says only
 * whether it can read each user's key; the key never reaches the browser.
 */
export async function listKeyUsers(
  connection: AdminConnection,
): Promise<KeyUser[]> {
  const users = await call<Array<Omit<KeyUser, 'role'> & { role: string }>>(
    connection,
    'GET',
    `/api/v1/admin/accounts/${encodeURIComponent(connection.accountId)}/users`,
    { query: { include_credentials: false } },
  )
  return users.filter((user): user is KeyUser =>
    KEY_USER_ROLES.includes(user.role),
  )
}

/**
 * Issues a key; the response is the only time the secret is available. For a
 * `user_id`, OpenViking Server sends that user's stored key to the gateway.
 */
export const issueKey = (connection: AdminConnection, request: KeyRequest) =>
  send<IssuedKey>(connection, 'POST', ['keys'], { body: request })

export const revokeKey = (connection: AdminConnection, id: string) =>
  send<{ deleted: boolean }>(connection, 'DELETE', ['keys', id])

/** Starts a fresh OpenViking session for one conversation of a key. */
export const resyncCapture = (
  connection: AdminConnection,
  keyId: string,
  target: ResyncTarget,
) =>
  send<{ status: string; message: string }>(
    connection,
    'POST',
    ['keys', keyId, 'capture', 'reset'],
    { body: target },
  )

/** Revokes every key of an OpenViking user and deletes their gateway conversations. */
export const deleteUserData = (connection: AdminConnection, userId: string) =>
  send<{ deleted: boolean }>(connection, 'DELETE', ['users', userId, 'data'])

/**
 * Random id for a new upstream or profile. Uses `getRandomValues`, which,
 * unlike `randomUUID`, also works when Studio is served over plain HTTP.
 */
export function newObjectId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(8))
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join(
    '',
  )
}
