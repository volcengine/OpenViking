import {
  getManagementKey,
  lockManagementKey,
} from '#/features/admin/gateway/lib/management-key'
import type { AdminConnection } from '#/shared/api/admin'
import { createAdminClient } from '#/shared/api/admin'

import { getOvResult, isOvClientError } from '#/shared/api/ov-client/index'

/** Credentials captured with the query scope; undefined reads the current tab key. */
export type GatewayConnection = AdminConnection & { managementKey?: string }

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
/** Header the upstream key is sent in. */
export type KeyHeader = 'authorization' | 'api-key' | 'x-api-key'

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
  /** Append the selected protocol's endpoint path to base_url. */
  append_endpoint_path: boolean
  auth_mode: AuthMode
  key_header: KeyHeader
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
  header_names?: string[]
}

/**
 * Body of `PUT upstreams/{id}`. A blank `api_key` keeps the stored key; a
 * header with a blank value keeps its stored value; omitted headers are retained.
 * Changing base_url clears retained API-key and header secrets.
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
  show_recall: boolean
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
  tool_max_rounds: number | null
  tool_timeout_seconds: number
  tool_result_bytes: number
  tool_total_seconds: number | null
  tool_total_tokens: number | null
  /** Clients may override the other settings with `X-OpenViking-Context`. */
  client_overrides: boolean
}

export type Profile = ProfileSettings & { id: string; revision: number }

/** A gateway key as listed; the secret itself is only returned once. */
export type GatewayKey = {
  id: string
  revision: number
  name: string
  /**
   * An OpenViking API key used directly. It stores no binding, so name,
   * policy_id, upstream_ids and models are empty; the gateway applies the
   * account's defaults to each request.
   */
  source?: 'openviking'
  policy_id: string
  upstream_ids: string[]
  models: string[]
  user_id: string
  /** First characters of the secret: 12 of a gateway key (`ovgw_Ab3dE9x`), 8 of an OpenViking API key. */
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
export type KeyOwner = { user_id?: string; openviking_key?: string }

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
  /**
   * False when the server can't read the user's key, e.g. it keeps only a
   * hash. OpenViking servers that predate the field omit it.
   */
  api_key_available?: boolean
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

/** A conversation whose latest long model calls all missed the prompt cache. */
export type CacheMiss = {
  upstream_id: string
  model: string
  /** Long calls in a row without a cache hit. */
  streak: number
  /** Epoch seconds of the newest call. */
  last_at: number
}

export type CacheAlerts = {
  /** Conversations missing the cache; null without any. */
  misses: {
    conversations: number
    /** Shortest streak and smallest first-call input among them. */
    streak: number
    input_tokens: number
    /** All are Anthropic conversations that never wrote the cache either. */
    no_writes: boolean
    /** The newest three conversations. */
    recent: CacheMiss[]
  } | null
  /** Ids of upstreams whose long calls have not hit the cache lately, sorted. */
  unreported: string[]
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
  /** Conversations and upstreams missing the prompt cache; null without any. */
  cache_alerts: CacheAlerts | null
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
  /**
   * Why compaction failed. The full history was sent, except for
   * `still_over_threshold`, where the summary was written and kept.
   */
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
  /** Profile fields the client set with `X-OpenViking-Context`; values are not logged. */
  context_override?: string[]
}

/** Where clients reach the gateway. */
export type ConnectionInfo = {
  base_url: string
  public_url_configured: boolean
}

export type GatewayAccess = {
  managed: boolean
  can_write_objects: boolean
  can_import_vlm: boolean
  can_issue_for_others: boolean
  role: string
  account_id: string
  user_id: string
  warnings: string[]
}

export type VlmImportResult = {
  imported: Array<{ id: string; name: string }>
  needs_confirmation: Array<{ id: string; name: string }>
  skipped: Array<{ id: string; reason: string }>
}

export type UpstreamTestResult = {
  url?: string
  ok: boolean
  kind?: 'connection' | 'model'
  status?: number
  reason?: string
  response?: {
    http_version: string
    status: number
    reason: string
    headers: Record<string, string | string[]>
    body: string
    truncated?: boolean
  }
}

/** Conversation to resync, taken from a log record. */
export type ResyncTarget = { session: string; protocol: Protocol }

export type GatewayErrorReason = string

const UNAVAILABLE = [
  'not_enabled',
  'trusted_mode_unsupported',
  'openviking_unreachable',
]

/** A failed gateway call: HTTP status, the server's own message, and a category. */
export class GatewayError extends Error {
  readonly status?: number
  readonly detail: string
  readonly reason: GatewayErrorReason
  readonly keyCount?: number
  readonly requiredVersion?: string

  constructor(
    detail: string,
    status?: number,
    cause?: unknown,
    reason?: GatewayErrorReason,
    metadata?: Record<string, unknown>,
  ) {
    super(detail, cause ? { cause } : undefined)
    this.name = 'GatewayError'
    this.detail = detail
    this.status = status
    this.reason = reason ?? classify(status)
    this.keyCount =
      typeof metadata?.key_count === 'number' ? metadata.key_count : undefined
    this.requiredVersion =
      typeof metadata?.required_version === 'string'
        ? metadata.required_version
        : undefined
  }

  /** True when the gateway itself is off, misconfigured, down or missing. */
  get unavailable(): boolean {
    return UNAVAILABLE.includes(this.reason)
  }
}

const STATUS_REASONS: Partial<Record<number, GatewayErrorReason>> = {
  400: 'invalid',
  401: 'unauthorized',
  403: 'forbidden',
  404: 'not_found',
  409: 'conflict',
  422: 'invalid',
}

function classify(status?: number): GatewayErrorReason {
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

function readReason(body: unknown): string | undefined {
  return isRecord(body) && typeof body.reason === 'string'
    ? body.reason
    : undefined
}

/** Normalizes anything a gateway call can throw into a `GatewayError`. */
export function toGatewayError(error: unknown): GatewayError {
  if (error instanceof GatewayError) return error
  if (isOvClientError(error)) {
    return new GatewayError(
      readDetail(error.responseBody) ?? error.message,
      error.statusCode,
      error,
      readReason(error.responseBody),
      isRecord(error.responseBody) ? error.responseBody : undefined,
    )
  }
  return new GatewayError(
    error instanceof Error ? error.message : String(error),
    undefined,
    error,
  )
}

const BASE_PATH = '/gateway/admin'

type Method = 'GET' | 'POST' | 'PUT' | 'DELETE'

type RequestOptions = {
  query?: Record<string, unknown>
  body?: unknown
  headers?: Record<string, string>
}

/** One OpenViking Server call; any failure becomes a `GatewayError`. */
async function call<T>(
  connection: GatewayConnection,
  method: Method,
  url: string,
  options: RequestOptions = {},
): Promise<T> {
  const body =
    options.body === undefined
      ? {}
      : {
          body: options.body,
          headers: { 'Content-Type': 'application/json', ...options.headers },
        }
  try {
    return await getOvResult<T>(
      createAdminClient(connection).request({
        method,
        url,
        query: options.query,
        headers: options.headers,
        ...body,
      }),
    )
  } catch (error) {
    throw toGatewayError(error)
  }
}

/** Calls the gateway's management API through OpenViking Server. */
async function send<T>(
  connection: GatewayConnection,
  method: Method,
  segments: string[],
  options?: Omit<RequestOptions, 'headers'>,
): Promise<T> {
  const url = [BASE_PATH, ...segments.map(encodeURIComponent)].join('/')
  const managementKey =
    connection.managementKey ?? getManagementKey(connection.baseUrl)
  try {
    return await call<T>(connection, method, url, {
      ...options,
      query: { ...options?.query, account_id: connection.accountId },
      headers: managementKey
        ? { 'X-OpenViking-Gateway-Management-Key': managementKey }
        : undefined,
    })
  } catch (error) {
    const failure = toGatewayError(error)
    // Lock only a stored key, not a candidate or a key replaced meanwhile.
    if (
      failure.reason === 'management_key_invalid' &&
      managementKey === getManagementKey(connection.baseUrl)
    ) {
      lockManagementKey(connection.baseUrl, true)
    }
    throw failure
  }
}

/**
 * Gateway address for clients; also the layout's availability probe. A 404
 * means the OpenViking server has no OpenViking Gateway management at all.
 */
export async function getConnectionInfo(
  connection: GatewayConnection,
): Promise<ConnectionInfo> {
  try {
    return await send<ConnectionInfo>(connection, 'GET', ['guides'])
  } catch (error) {
    const failure = toGatewayError(error)
    if (failure.status !== 404) throw failure
    throw new GatewayError(failure.detail, 404, failure, 'not_enabled')
  }
}

/** Pass `managementKey` in the connection to check a candidate key. */
export async function getAccess(
  connection: GatewayConnection,
): Promise<GatewayAccess> {
  try {
    return await send<GatewayAccess>(connection, 'GET', ['access'])
  } catch (error) {
    const failure = toGatewayError(error)
    if (failure.status === 404)
      throw new GatewayError(failure.detail, 404, failure, 'not_enabled')
    throw failure
  }
}

export const importVlm = (connection: GatewayConnection) =>
  send<VlmImportResult>(connection, 'POST', ['upstreams', 'import-vlm'])

export const getOverview = (connection: GatewayConnection) =>
  send<Overview>(connection, 'GET', ['overview'])

/** Newest-first request log; the gateway caps `limit` at 1,000. */
export const listLogs = (connection: GatewayConnection, limit = 200) =>
  send<LogRecord[]>(connection, 'GET', ['logs'], { query: { limit } })

export const listUpstreams = (connection: GatewayConnection) =>
  send<Upstream[]>(connection, 'GET', ['upstreams'])

/** OpenViking MCP tools, read with the caller's OpenViking key (root: a key issued in this account). */
export const listTools = (connection: GatewayConnection) =>
  send<GatewayTool[]>(connection, 'GET', ['tools'])

/** Creates or replaces an upstream. Build the body with `toUpstreamInput`. */
export const saveUpstream = (
  connection: GatewayConnection,
  id: string,
  input: UpstreamInput,
) => send<Upstream>(connection, 'PUT', ['upstreams', id], { body: input })

/** Fails with reason `conflict` while keys still use the upstream. */
export const deleteUpstream = (connection: GatewayConnection, id: string) =>
  send<{ deleted: boolean }>(connection, 'DELETE', ['upstreams', id])

/** Checks connectivity, or sends one small generation request when a model is given. */
export const testUpstream = (
  connection: GatewayConnection,
  id: string,
  model?: string,
) =>
  send<UpstreamTestResult>(connection, 'POST', ['upstreams', id, 'test'], {
    body: model ? { model } : {},
  })

export const listProfiles = (connection: GatewayConnection) =>
  send<Profile[]>(connection, 'GET', ['policies'])

/** Creates or replaces a profile. Build the body with `toProfileSettings`. */
export const saveProfile = (
  connection: GatewayConnection,
  id: string,
  settings: ProfileSettings,
) => send<Profile>(connection, 'PUT', ['policies', id], { body: settings })

/** Fails with reason `conflict` while keys still use the profile. */
export const deleteProfile = (connection: GatewayConnection, id: string) =>
  send<{ deleted: boolean }>(connection, 'DELETE', ['policies', id])

export const listKeys = (connection: GatewayConnection) =>
  send<GatewayKey[]>(connection, 'GET', ['keys'])

const KEY_USER_ROLES: readonly string[] = ['admin', 'user']

/**
 * Users of the current account a key can act as. The server says only
 * whether it can read each user's key; the key never reaches the browser.
 */
export async function listKeyUsers(
  connection: GatewayConnection,
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
export const issueKey = (connection: GatewayConnection, request: KeyRequest) =>
  send<IssuedKey>(connection, 'POST', ['keys'], { body: request })

export const revokeKey = (connection: GatewayConnection, id: string) =>
  send<{ deleted: boolean }>(connection, 'DELETE', ['keys', id])

/** Starts a fresh OpenViking session for one conversation of a key. */
export const resyncCapture = (
  connection: GatewayConnection,
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
export const deleteUserData = (connection: GatewayConnection, userId: string) =>
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
