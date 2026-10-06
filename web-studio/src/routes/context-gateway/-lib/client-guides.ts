import type { Protocol, Upstream } from './api'

/** Clients with a setup guide, in display order. */
export type ClientId =
  | 'claude-code'
  | 'codex'
  | 'chat'
  | 'open-webui'
  | 'opencode'
  | 'pi'
  | 'ark'

export const CLIENT_IDS: ClientId[] = [
  'claude-code',
  'codex',
  'chat',
  'open-webui',
  'opencode',
  'pi',
  'ark',
]

/** Protocol each client calls; the gateway needs an enabled upstream for it. */
export const CLIENT_PROTOCOLS: Record<ClientId, Protocol[]> = {
  'claude-code': ['anthropic'],
  codex: ['responses'],
  chat: ['chat'],
  'open-webui': ['chat'],
  opencode: ['chat'],
  pi: ['chat'],
  ark: ['chat', 'responses', 'anthropic'],
}

/** Enabled upstreams that speak a protocol the client calls. */
export function servingUpstreams(
  client: ClientId,
  upstreams: Upstream[],
): Upstream[] {
  const protocols = CLIENT_PROTOCOLS[client]
  return upstreams.filter(
    (upstream) => upstream.enabled && protocols.includes(upstream.protocol),
  )
}

export type SnippetLanguage = 'bash' | 'toml' | 'json' | 'python' | 'text'

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

function exportKey(key: string): Snippet {
  return {
    id: 'key',
    language: 'bash',
    code: `export ${KEY_ENV}=${shellQuote(key)}`,
  }
}

const builders: Record<
  ClientId,
  (base: string, key: string, model: string) => Snippet[]
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
        'name = "OpenViking Context Gateway"',
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
  opencode: (base, key, model) => [
    {
      id: 'config',
      language: 'json',
      filename: 'opencode.json',
      code: json({
        provider: {
          openviking: {
            npm: '@ai-sdk/openai-compatible',
            name: 'OpenViking',
            options: { baseURL: `${base}/v1`, apiKey: `{env:${KEY_ENV}}` },
            models: { [model]: {} },
          },
        },
      }),
    },
    exportKey(key),
  ],
  pi: (base, key, model) => [
    {
      id: 'config',
      language: 'json',
      code: json({
        providers: {
          openviking: {
            baseUrl: `${base}/v1`,
            apiKey: KEY_ENV,
            api: 'openai-completions',
            models: [{ id: model }],
          },
        },
      }),
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
  return builders[client](
    base,
    input.key?.trim() || KEY_PLACEHOLDER,
    input.model?.trim() || MODEL_PLACEHOLDER,
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
  const slug =
    page === 'guide' ? '15-context-gateway' : '22-context-gateway-operations'
  return `https://docs.openviking.ai/${locale}/guides/${slug}`
}
