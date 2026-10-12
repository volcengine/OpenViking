import type { Protocol, Upstream } from './api'

/** Clients with a setup guide, in display order. */
export type ClientId =
  | 'claude-code'
  | 'codex'
  | 'chat'
  | 'open-webui'
  | 'opencode'
  | 'pi'
  | 'dsh'
  | 'ark'

export const CLIENT_IDS: ClientId[] = [
  'claude-code',
  'codex',
  'chat',
  'open-webui',
  'opencode',
  'pi',
  'dsh',
  'ark',
]

/**
 * Protocols each client can call, preferred first. The gateway needs an
 * enabled upstream for the one the client is set up with.
 */
export const CLIENT_PROTOCOLS: Record<ClientId, Protocol[]> = {
  'claude-code': ['anthropic'],
  codex: ['responses'],
  chat: ['chat'],
  'open-webui': ['chat'],
  opencode: ['chat', 'anthropic', 'responses'],
  pi: ['chat', 'anthropic', 'responses'],
  dsh: ['chat', 'anthropic', 'responses'],
  ark: ['chat', 'responses', 'anthropic'],
}

/**
 * Clients configured for one protocol at a time, so their snippets change with
 * the protocol picked. The other multi-protocol clients reach all of theirs
 * with one setup.
 */
const PICKS_PROTOCOL: ReadonlySet<ClientId> = new Set(['opencode', 'pi', 'dsh'])

/** Protocols the user picks between for a client; empty when there is no choice. */
export function protocolChoices(client: ClientId): Protocol[] {
  return PICKS_PROTOCOL.has(client) ? CLIENT_PROTOCOLS[client] : []
}

/**
 * Protocols the client's current setup calls: the picked one (or a valid
 * default) for clients set up per protocol, else all of them.
 */
export function activeProtocols(
  client: ClientId,
  protocol?: Protocol,
): Protocol[] {
  const choices = protocolChoices(client)
  if (!choices.length) return CLIENT_PROTOCOLS[client]
  return [protocol && choices.includes(protocol) ? protocol : choices[0]]
}

/** Enabled upstreams that speak one of `protocols`. */
function enabledFor(protocols: Protocol[], upstreams: Upstream[]): Upstream[] {
  return upstreams.filter(
    (upstream) => upstream.enabled && protocols.includes(upstream.protocol),
  )
}

/**
 * Enabled upstreams the client can reach: through any protocol it supports,
 * or only through `protocol` when given.
 */
export function servingUpstreams(
  client: ClientId,
  upstreams: Upstream[],
  protocol?: Protocol,
): Upstream[] {
  return enabledFor(
    protocol ? activeProtocols(client, protocol) : CLIENT_PROTOCOLS[client],
    upstreams,
  )
}

/**
 * Protocol to set a client up with when none was picked: the first it supports
 * that some enabled upstream speaks, else its preferred one.
 */
export function defaultProtocol(
  client: ClientId,
  upstreams: Upstream[] = [],
): Protocol {
  const protocols = CLIENT_PROTOCOLS[client]
  return (
    protocols.find((protocol) => enabledFor([protocol], upstreams).length) ??
    protocols[0]
  )
}

export type SnippetLanguage =
  | 'bash'
  | 'toml'
  | 'json'
  | 'yaml'
  | 'python'
  | 'text'

/** One copyable block. `id` names its caption under `connect.snippets.*`. */
export type Snippet = {
  id: string
  language: SnippetLanguage
  code: string
  /** File the snippet belongs in, when there is a conventional one. */
  filename?: string
}

/** Values filled into snippets; missing ones become placeholders. */
export type GuideInput = {
  baseUrl: string
  /** Protocol for clients set up per protocol; defaults to the preferred one. */
  protocol?: Protocol
  key?: string
  model?: string
}

export const KEY_PLACEHOLDER = '<gateway-key>'
export const MODEL_PLACEHOLDER = '<model>'
export const SESSION_PLACEHOLDER = '<conversation-id>'
/** Environment variable the config-file snippets read the gateway key from. */
export const KEY_ENV = 'OPENVIKING_GATEWAY_KEY'

/** Quotes a value for POSIX shells when it contains special characters. */
export function shellQuote(value: string): string {
  return /^[\w@%+=:,./-]+$/.test(value)
    ? value
    : `'${value.replaceAll("'", `'"'"'`)}'`
}

function json(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

/** `api` value pi and DSH (which builds on pi's model layer) use per protocol. */
const PI_APIS: Record<Protocol, string> = {
  chat: 'openai-completions',
  responses: 'openai-responses',
  anthropic: 'anthropic-messages',
}

/** Base URL for Anthropic SDKs, which append `/v1/messages` themselves. */
function piBaseUrl(base: string, protocol: Protocol): string {
  return protocol === 'anthropic' ? base : `${base}/v1`
}

/** AI SDK package OpenCode loads per protocol; every one takes `/v1`. */
const OPENCODE_PACKAGES: Record<Protocol, string> = {
  chat: '@ai-sdk/openai-compatible',
  responses: '@ai-sdk/openai',
  anthropic: '@ai-sdk/anthropic',
}

function exportKey(key: string): Snippet {
  return {
    id: 'key',
    language: 'bash',
    code: `export ${KEY_ENV}=${shellQuote(key)}`,
  }
}

const builders: Record<
  ClientId,
  (base: string, key: string, model: string, protocol: Protocol) => Snippet[]
> = {
  'claude-code': (base, key) => [
    {
      id: 'env',
      language: 'bash',
      code: [
        `export ANTHROPIC_BASE_URL=${shellQuote(base)}`,
        `export ANTHROPIC_AUTH_TOKEN=${shellQuote(key)}`,
        'export CLAUDE_CODE_GATEWAY_HINT_HEADERS=1',
      ].join('\n'),
    },
  ],
  codex: (base, key, model) => [
    {
      id: 'config',
      language: 'toml',
      filename: '~/.codex/config.toml',
      code: [
        'model_provider = "openviking"',
        `model = ${JSON.stringify(model)}`,
        '',
        '[model_providers.openviking]',
        'name = "OpenViking Gateway"',
        `base_url = ${JSON.stringify(`${base}/v1`)}`,
        'wire_api = "responses"',
        `env_key = "${KEY_ENV}"`,
      ].join('\n'),
    },
    exportKey(key),
  ],
  chat: (base, key, model) => [
    {
      id: 'settings',
      language: 'text',
      code: [
        `Base URL: ${base}/v1`,
        `API key: ${key}`,
        `Model: ${model}`,
        `X-OpenViking-Session: ${SESSION_PLACEHOLDER}`,
      ].join('\n'),
    },
    {
      id: 'python',
      language: 'python',
      code: [
        'from openai import OpenAI',
        '',
        `client = OpenAI(base_url=${JSON.stringify(`${base}/v1`)}, api_key=${JSON.stringify(key)})`,
        'response = client.chat.completions.create(',
        `    model=${JSON.stringify(model)},`,
        '    messages=[{"role": "user", "content": "Hello"}],',
        `    extra_headers={"X-OpenViking-Session": ${JSON.stringify(SESSION_PLACEHOLDER)}},`,
        ')',
      ].join('\n'),
    },
    {
      id: 'curl',
      language: 'bash',
      code: [
        `curl ${shellQuote(`${base}/v1/chat/completions`)} \\`,
        `  -H ${shellQuote(`Authorization: Bearer ${key}`)} \\`,
        "  -H 'Content-Type: application/json' \\",
        `  -H ${shellQuote(`X-OpenViking-Session: ${SESSION_PLACEHOLDER}`)} \\`,
        `  -d ${shellQuote(JSON.stringify({ model, messages: [{ role: 'user', content: 'Hello' }] }))}`,
      ].join('\n'),
    },
  ],
  'open-webui': (base, key) => [
    {
      id: 'connection',
      language: 'text',
      code: [`URL: ${base}/v1`, `Key: ${key}`].join('\n'),
    },
    {
      id: 'headers',
      language: 'json',
      code: json({
        'X-OpenViking-Session': '{{CHAT_ID}}',
        'X-OpenViking-Task': '{{TASK}}',
      }),
    },
    { id: 'env', language: 'bash', code: 'RAG_SYSTEM_CONTEXT=true' },
  ],
  opencode: (base, key, model, protocol) => [
    {
      id: 'config',
      language: 'json',
      filename: 'opencode.json',
      code: json({
        provider: {
          openviking: {
            npm: OPENCODE_PACKAGES[protocol],
            name: 'OpenViking',
            options: { baseURL: `${base}/v1`, apiKey: `{env:${KEY_ENV}}` },
            models: { [model]: {} },
          },
        },
      }),
    },
    exportKey(key),
  ],
  pi: (base, key, model, protocol) => [
    {
      id: 'config',
      language: 'json',
      filename: '~/.pi/agent/models.json',
      code: json({
        providers: {
          openviking: {
            baseUrl: piBaseUrl(base, protocol),
            apiKey: `$${KEY_ENV}`,
            api: PI_APIS[protocol],
            models: [{ id: model }],
          },
        },
      }),
    },
    exportKey(key),
  ],
  dsh: (base, key, model, protocol) => [
    {
      id: 'config',
      language: 'yaml',
      filename: '~/.dsh/profiles/web/cordis.patch.yml',
      code: [
        '- id: llm-pi-ai',
        '  config:',
        '    providers:',
        '      openviking:',
        `        api: ${PI_APIS[protocol]}`,
        `        baseURL: ${JSON.stringify(piBaseUrl(base, protocol))}`,
        `        apiKeyEnv: ${KEY_ENV}`,
        '        models:',
        `          - id: ${JSON.stringify(model)}`,
      ].join('\n'),
    },
    exportKey(key),
  ],
  ark: (base, key) => [
    {
      id: 'endpoints',
      language: 'text',
      code: [
        `Chat Completions / Responses: ${base}/api/v3`,
        `Anthropic Messages: ${base}/api/compatible`,
        `API key: ${key}`,
      ].join('\n'),
    },
    {
      id: 'python',
      language: 'python',
      code: [
        'from volcenginesdkarkruntime import Ark',
        '',
        `client = Ark(base_url=${JSON.stringify(`${base}/api/v3`)}, api_key=${JSON.stringify(key)})`,
      ].join('\n'),
    },
  ],
}

/**
 * Copy-ready snippets for one client. The gateway key and model fall back to
 * `<gateway-key>` and `<model>` placeholders.
 */
export function clientSnippets(client: ClientId, input: GuideInput): Snippet[] {
  const base = input.baseUrl.trim().replace(/\/+$/, '')
  const [protocol] = activeProtocols(client, input.protocol)
  return builders[client](
    base,
    input.key?.trim() || KEY_PLACEHOLDER,
    input.model?.trim() || MODEL_PLACEHOLDER,
    protocol,
  )
}

/** Request headers the gateway reads a conversation id from, first match wins. */
export const SESSION_HEADERS = [
  'X-OpenViking-Session',
  'thread-id',
  'x-claude-code-session-id',
  'x-opencode-session-id',
  'x-session-id',
  'session-id',
]

/** OpenViking docs page for the gateway, in the UI language. */
export function gatewayDocsUrl(
  page: 'guide' | 'operations',
  language?: string,
): string {
  const locale = language?.startsWith('zh') ? 'zh' : 'en'
  const slug = page === 'guide' ? '15-gateway' : '22-gateway-operations'
  return `https://docs.openviking.ai/${locale}/guides/${slug}`
}
