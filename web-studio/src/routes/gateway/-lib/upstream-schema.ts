import type { Protocol, Upstream, UpstreamInput, Vendor } from './api'
import { checkNumber, checkRequired, collect } from './validation'
import type { NumberRule, ValidationErrors } from './validation'

/** A new upstream; identical to the gateway's own defaults. */
export const UPSTREAM_DEFAULTS: UpstreamInput = {
  name: '',
  protocol: 'chat',
  base_url: '',
  api_key: '',
  auth_mode: 'managed',
  headers: {},
  models: [],
  aliases: {},
  priority: 0,
  enabled: true,
  vendor: 'generic',
  allow_gateway_tools: true,
  replay_reasoning: null,
  coding_plan: false,
  allow_coding_plan: false,
  cache_min_tokens: 1024,
  context_windows: {},
}

export const PROTOCOLS: Protocol[] = ['anthropic', 'chat', 'responses']
export const VENDORS: Vendor[] = [
  'generic',
  'anthropic',
  'openai',
  'deepseek',
  'ark',
  'byteplus',
]

/**
 * Volcano Engine Ark and BytePlus ModelArk, its international edition, which
 * share Ark's paths and prompt caching.
 */
export const ARK_VENDORS: Vendor[] = ['ark', 'byteplus']

/**
 * Protocols each provider offers, the only ones the editor lets you pick. The
 * gateway itself accepts any pair; Generic covers unlisted providers and
 * compatible proxies, so it allows all three.
 */
export const VENDOR_PROTOCOLS: Record<Vendor, Protocol[]> = {
  generic: PROTOCOLS,
  anthropic: ['anthropic'],
  openai: ['chat', 'responses'],
  deepseek: PROTOCOLS,
  ark: PROTOCOLS,
  byteplus: PROTOCOLS,
}

/**
 * Providers whose models expect their reasoning back in later turns; the
 * gateway restores reasoning for them unless the upstream turns it off.
 */
export const REASONING_VENDORS: Vendor[] = ['deepseek', 'ark', 'byteplus']

/** Whether the gateway restores dropped reasoning for this upstream. */
export function replaysReasoning(
  upstream: Pick<UpstreamInput, 'vendor' | 'replay_reasoning'>,
): boolean {
  return (
    upstream.replay_reasoning ?? REASONING_VENDORS.includes(upstream.vendor)
  )
}

/**
 * The stored setting for a switch at `checked`: null while it matches the
 * vendor default, so the upstream keeps following its vendor.
 */
export function replayReasoningSetting(
  vendor: Vendor,
  checked: boolean,
): boolean | null {
  return checked === REASONING_VENDORS.includes(vendor) ? null : checked
}

/** Each provider's own API address; Generic has none. */
const DEFAULT_BASE_URLS: Partial<Record<Vendor, string>> = {
  anthropic: 'https://api.anthropic.com',
  openai: 'https://api.openai.com/v1',
  deepseek: 'https://api.deepseek.com',
  ark: 'https://ark.cn-beijing.volces.com',
  byteplus: 'https://ark.ap-southeast.bytepluses.com',
}

/**
 * Protocols `vendor` offers. A provider id this Studio doesn't know yet (from
 * a newer gateway) is treated like Generic.
 */
function vendorProtocols(vendor: Vendor): Protocol[] {
  const known: Partial<Record<string, Protocol[]>> = VENDOR_PROTOCOLS
  return known[vendor] ?? PROTOCOLS
}

export function supportsProtocol(vendor: Vendor, protocol: Protocol): boolean {
  return vendorProtocols(vendor).includes(protocol)
}

/**
 * The protocol to use after switching to `vendor`: `current` when the vendor
 * offers it, else Chat Completions, else the vendor's first protocol.
 */
export function protocolFor(vendor: Vendor, current: Protocol): Protocol {
  if (supportsProtocol(vendor, current)) return current
  if (supportsProtocol(vendor, 'chat')) return 'chat'
  return vendorProtocols(vendor)[0]
}

/**
 * The base URL the editor fills in for `vendor` and `protocol`; empty for
 * Generic and for a provider id this Studio doesn't know.
 */
export function defaultBaseUrl(vendor: Vendor, protocol: Protocol): string {
  // DeepSeek serves Anthropic Messages under a path of its own.
  if (vendor === 'deepseek' && protocol === 'anthropic') {
    return 'https://api.deepseek.com/anthropic'
  }
  const known: Partial<Record<string, string>> = DEFAULT_BASE_URLS
  return known[vendor] ?? ''
}

/** True when the base URL is the default for the upstream's provider and protocol. */
export function usesDefaultBaseUrl(
  upstream: Pick<UpstreamInput, 'base_url' | 'vendor' | 'protocol'>,
): boolean {
  return (
    upstream.base_url.trim().replace(/\/+$/, '') ===
    defaultBaseUrl(upstream.vendor, upstream.protocol)
  )
}

/**
 * The base URL after switching the upstream to `vendor` and `protocol`: the
 * new default while the URL is empty or still the previous default, so a URL
 * the admin typed is never replaced.
 */
export function baseUrlAfterSwitch(
  upstream: Pick<UpstreamInput, 'base_url' | 'vendor' | 'protocol'>,
  vendor: Vendor,
  protocol: Protocol,
): string {
  return !upstream.base_url.trim() || usesDefaultBaseUrl(upstream)
    ? defaultBaseUrl(vendor, protocol)
    : upstream.base_url
}

/** Client endpoint each protocol serves on the gateway. */
export const PROTOCOL_PATHS: Record<Protocol, string> = {
  anthropic: '/v1/messages',
  chat: '/v1/chat/completions',
  responses: '/v1/responses',
}

/** Header names the gateway refuses to forward from an upstream. */
export const RESERVED_HEADERS = [
  'host',
  'content-length',
  'transfer-encoding',
  'connection',
]

/** Header clients send their own provider key in, for passthrough upstreams. */
export const UPSTREAM_KEY_HEADER = 'X-OpenViking-Upstream-Key'

export const UPSTREAM_LIMITS = {
  priority: { integer: true },
  cache_min_tokens: { min: 0, integer: true, unit: 'tokens' },
  context_window: { min: 1024, integer: true, unit: 'tokens' },
} satisfies Record<string, NumberRule>

/**
 * Save body for an existing upstream: every field as stored, a blank API key
 * and blank header values, which the gateway reads as "keep what is stored".
 */
export function toUpstreamInput(upstream: Upstream): UpstreamInput {
  const input: Record<string, unknown> = {
    ...UPSTREAM_DEFAULTS,
    ...upstream,
    api_key: '',
    headers: Object.fromEntries(
      upstream.header_names.map((name) => [name, '']),
    ),
  }
  for (const field of ['id', 'revision', 'has_api_key', 'header_names']) {
    delete input[field]
  }
  return input as UpstreamInput
}

/** Model names clients can request through this upstream: models plus alias names. */
export function servedModels(
  upstream: Pick<UpstreamInput, 'models' | 'aliases'>,
): string[] {
  return [...new Set([...upstream.models, ...Object.keys(upstream.aliases)])]
}

/** True for an absolute http(s) URL without credentials, query or fragment. */
export function isValidBaseUrl(value: string): boolean {
  let url: URL
  try {
    url = new URL(value.trim())
  } catch {
    return false
  }
  return (
    (url.protocol === 'http:' || url.protocol === 'https:') &&
    Boolean(url.hostname) &&
    !url.username &&
    !url.password &&
    !url.search &&
    !url.hash &&
    !/[?#]/.test(value)
  )
}

/**
 * Where the gateway sends a request for `path` (a `/v1/…` client path),
 * following its rules: a base ending in `/v1` does not get a second `/v1`;
 * Ark and ModelArk map Messages to `/api/compatible/v1` and everything else
 * to `/api/v3`.
 */
export function upstreamUrl(
  baseUrl: string,
  vendor: Vendor,
  path: string,
): string {
  const base = baseUrl.trim().replace(/\/+$/, '')
  const basePath = new URL(base).pathname.replace(/\/+$/, '')
  if (ARK_VENDORS.includes(vendor)) {
    const suffix = path.replace(/^\/v1/, '')
    const prefix = path.startsWith('/v1/messages')
      ? '/api/compatible/v1'
      : '/api/v3'
    return basePath.endsWith(prefix) ? base + suffix : base + prefix + suffix
  }
  if (basePath.endsWith('/v1') && path.startsWith('/v1/')) {
    return base + path.slice(3)
  }
  return base + path
}

/**
 * The full URL requests for `protocol` go to, for the "Requests go to …" hint;
 * empty while the base URL is not valid yet.
 */
export function endpointPreview(
  upstream: Pick<UpstreamInput, 'base_url' | 'vendor' | 'protocol'>,
  protocol: Protocol = upstream.protocol,
): string {
  if (!isValidBaseUrl(upstream.base_url)) return ''
  return upstreamUrl(
    upstream.base_url,
    upstream.vendor,
    PROTOCOL_PATHS[protocol],
  )
}

/**
 * Checks an upstream before saving. Pass the stored upstream when editing so
 * blank secrets that will be kept are accepted. Keys are field names.
 */
export function validateUpstream(
  input: UpstreamInput,
  stored?: Upstream,
): ValidationErrors {
  const errors: ValidationErrors = {}
  collect(errors, 'name', checkRequired(input.name))
  // Only an upstream saved through the API can hold such a pair. The values
  // are ids; the message nests their labels.
  if (!supportsProtocol(input.vendor, input.protocol)) {
    collect(errors, 'protocol', {
      key: 'validation.protocolUnsupported',
      values: { vendor: input.vendor, protocol: input.protocol },
    })
  }
  collect(
    errors,
    'base_url',
    checkRequired(input.base_url) ??
      (isValidBaseUrl(input.base_url)
        ? undefined
        : { key: 'validation.baseUrl' }),
  )
  const apiKey = input.api_key.trim()
  if (apiKey.startsWith('sk-ant-oat')) {
    collect(errors, 'api_key', { key: 'validation.subscriptionKey' })
  }
  if (input.auth_mode === 'managed' && !apiKey && !stored?.has_api_key) {
    collect(errors, 'api_key', { key: 'validation.apiKeyRequired' })
  }
  const storedHeaders = stored?.header_names ?? []
  for (const [name, value] of Object.entries(input.headers)) {
    const values = { name }
    if (!name.trim() || /[\r\n]/.test(name + value)) {
      collect(errors, 'headers', { key: 'validation.headerInvalid', values })
    } else if (RESERVED_HEADERS.includes(name.trim().toLowerCase())) {
      collect(errors, 'headers', { key: 'validation.headerReserved', values })
    } else if (!value && !storedHeaders.includes(name)) {
      collect(errors, 'headers', { key: 'validation.headerValue', values })
    }
  }
  for (const [name, target] of Object.entries(input.aliases)) {
    if (!target.trim()) {
      collect(errors, 'aliases', {
        key: 'validation.aliasTarget',
        values: { name },
      })
    }
  }
  for (const [name, tokens] of Object.entries(input.context_windows)) {
    if (checkNumber(tokens, UPSTREAM_LIMITS.context_window)) {
      collect(errors, 'context_windows', {
        key: 'validation.contextWindow',
        values: { name, min: UPSTREAM_LIMITS.context_window.min },
      })
    }
  }
  collect(
    errors,
    'priority',
    checkNumber(input.priority, UPSTREAM_LIMITS.priority),
  )
  collect(
    errors,
    'cache_min_tokens',
    checkNumber(input.cache_min_tokens, UPSTREAM_LIMITS.cache_min_tokens),
  )
  return errors
}
