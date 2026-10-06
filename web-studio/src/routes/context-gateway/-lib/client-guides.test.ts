import { describe, expect, it } from 'vitest'

import {
  CLIENT_IDS,
  clientSnippets,
  gatewayDocsUrl,
  shellQuote,
} from './client-guides'

const base = 'https://ov.example.com/'

function code(client: Parameters<typeof clientSnippets>[0], id: string) {
  const snippet = clientSnippets(client, { baseUrl: base }).find(
    (item) => item.id === id,
  )
  if (!snippet) throw new Error(`missing ${client}/${id}`)
  return snippet.code
}

describe('clientSnippets', () => {
  it('builds snippets for every client with placeholders by default', () => {
    for (const client of CLIENT_IDS) {
      const snippets = clientSnippets(client, { baseUrl: base })
      expect(snippets.length).toBeGreaterThan(0)
      const text = snippets.map((snippet) => snippet.code).join('\n')
      expect(text).not.toContain('example.com//')
      expect(text).toContain('<gateway-key>')
    }
  })

  it('points Claude Code at the bare gateway address with a quoted placeholder', () => {
    expect(code('claude-code', 'env')).toBe(
      [
        'export ANTHROPIC_BASE_URL=https://ov.example.com',
        "export ANTHROPIC_AUTH_TOKEN='<gateway-key>'",
        'export CLAUDE_CODE_GATEWAY_HINT_HEADERS=1',
      ].join('\n'),
    )
  })

  it('sets the Codex provider and model at the TOML root', () => {
    const config = code('codex', 'config')
    const table = config.indexOf('[model_providers.openviking]')
    expect(config.indexOf('model_provider = "openviking"')).toBeLessThan(table)
    expect(config.indexOf('model = "<model>"')).toBeLessThan(table)
    expect(config).toContain('base_url = "https://ov.example.com/v1"')
    expect(config).toContain('wire_api = "responses"')
    expect(config).toContain('env_key = "OPENVIKING_GATEWAY_KEY"')
  })

  it('fills in a real key and model', () => {
    const snippets = clientSnippets('codex', {
      baseUrl: base,
      key: 'ovcg_abc',
      model: 'gpt-5',
    })
    const text = snippets.map((snippet) => snippet.code).join('\n')
    expect(text).toContain('model = "gpt-5"')
    expect(text).toContain('export OPENVIKING_GATEWAY_KEY=ovcg_abc')
    expect(text).not.toContain('<')
  })

  it('emits valid JSON for JSON configs', () => {
    for (const client of CLIENT_IDS) {
      for (const snippet of clientSnippets(client, { baseUrl: base })) {
        if (snippet.language === 'json') {
          expect(() => JSON.parse(snippet.code)).not.toThrow()
        }
      }
    }
    expect(JSON.parse(code('opencode', 'config'))).toMatchObject({
      provider: {
        openviking: {
          options: {
            baseURL: 'https://ov.example.com/v1',
            apiKey: '{env:OPENVIKING_GATEWAY_KEY}',
          },
          models: { '<model>': {} },
        },
      },
    })
    expect(JSON.parse(code('open-webui', 'headers'))).toEqual({
      'X-OpenViking-Session': '{{CHAT_ID}}',
      'X-OpenViking-Task': '{{TASK}}',
    })
  })

  it('sends the session header from chat clients', () => {
    expect(code('chat', 'curl')).toContain(
      "-H 'X-OpenViking-Session: <conversation-id>'",
    )
    expect(code('chat', 'python')).toContain(
      'base_url="https://ov.example.com/v1"',
    )
  })

  it('maps Ark-addressed clients to the native paths', () => {
    expect(code('ark', 'endpoints')).toContain('https://ov.example.com/api/v3')
    expect(code('ark', 'endpoints')).toContain(
      'https://ov.example.com/api/compatible',
    )
  })
})

it('quotes shell values only when needed', () => {
  expect(shellQuote('ovcg_Ab-3.x')).toBe('ovcg_Ab-3.x')
  expect(shellQuote('<gateway-key>')).toBe("'<gateway-key>'")
  expect(shellQuote("it's")).toBe(`'it'"'"'s'`)
})

it('links the docs in the UI language', () => {
  expect(gatewayDocsUrl('guide', 'en')).toBe(
    'https://docs.openviking.ai/en/guides/15-context-gateway',
  )
  expect(gatewayDocsUrl('operations', 'zh-CN')).toBe(
    'https://docs.openviking.ai/zh/guides/22-context-gateway-operations',
  )
})
