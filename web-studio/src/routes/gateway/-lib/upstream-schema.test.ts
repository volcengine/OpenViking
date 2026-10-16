import urlVectors from '../../../../../tests/fixtures/gateway_upstream_urls.json'
import { describe, expect, it } from 'vitest'

import type { Protocol, Upstream, UpstreamInput, Vendor } from './api'
import {
  PROTOCOLS,
  UPSTREAM_DEFAULTS,
  VENDORS,
  VENDOR_PROTOCOLS,
  baseUrlAfterSwitch,
  baseUrlChanged,
  baseUrlCorrection,
  defaultBaseUrl,
  endpointPreview,
  isValidBaseUrl,
  protocolFor,
  replayReasoningSetting,
  replaysReasoning,
  servedModels,
  supportsProtocol,
  toUpstreamInput,
  upstreamUrl,
  usesDefaultBaseUrl,
  validateUpstream,
} from './upstream-schema'

const stored: Upstream = {
  ...UPSTREAM_DEFAULTS,
  id: 'u1',
  revision: 4,
  name: 'OpenAI',
  base_url: 'https://api.openai.com/v1',
  models: ['gpt-5'],
  aliases: { fast: 'gpt-5-mini' },
  enabled: false,
  priority: 0,
  has_api_key: true,
  header_names: ['OpenAI-Organization'],
}

const input = (patch: Partial<UpstreamInput> = {}): UpstreamInput => ({
  ...UPSTREAM_DEFAULTS,
  name: 'Chat',
  base_url: 'https://api.example.com/v1',
  api_key: 'sk-test',
  ...patch,
})

describe('toUpstreamInput', () => {
  it('drops display-only fields and never resends secrets', () => {
    const body = toUpstreamInput(stored)
    for (const field of ['id', 'revision', 'has_api_key', 'header_names']) {
      expect(body).not.toHaveProperty(field)
    }
    expect(body.api_key).toBe('')
    expect(body.headers).toEqual({ 'OpenAI-Organization': '' })
    expect(body).toMatchObject({
      enabled: false,
      priority: 0,
      models: ['gpt-5'],
    })
    expect(Object.keys(body).sort()).toEqual(
      Object.keys(UPSTREAM_DEFAULTS).sort(),
    )
  })
})

it('lists models and alias names once', () => {
  expect(
    servedModels({ models: ['a', 'b'], aliases: { b: 'x', c: 'y' } }),
  ).toEqual(['a', 'b', 'c'])
})

describe('protocols per provider', () => {
  it('offers every protocol for Generic, DeepSeek and both Arks, and at least one everywhere', () => {
    for (const vendor of ['generic', 'deepseek', 'ark', 'byteplus'] as const) {
      expect(VENDOR_PROTOCOLS[vendor], vendor).toEqual(PROTOCOLS)
    }
    for (const vendor of VENDORS) {
      expect(VENDOR_PROTOCOLS[vendor].length, vendor).toBeGreaterThan(0)
    }
  })

  it.each([
    ['anthropic', 'anthropic', true],
    ['anthropic', 'chat', false],
    ['openai', 'anthropic', false],
    ['openai', 'responses', true],
    ['deepseek', 'anthropic', true],
    ['deepseek', 'responses', true],
  ] as const)('%s offers %s: %s', (vendor, protocol, offered) => {
    expect(supportsProtocol(vendor, protocol)).toBe(offered)
  })

  it.each([
    ['openai', 'responses', 'responses'],
    ['openai', 'anthropic', 'chat'],
    ['anthropic', 'chat', 'anthropic'],
    ['anthropic', 'responses', 'anthropic'],
    ['deepseek', 'responses', 'responses'],
    ['generic', 'anthropic', 'anthropic'],
  ] as const)('switching to %s from %s picks %s', (vendor, current, next) => {
    expect(protocolFor(vendor, current)).toBe(next)
  })

  it('treats a provider id it does not know like Generic', () => {
    const vendor = 'newcomer' as Vendor
    for (const protocol of PROTOCOLS) {
      expect(supportsProtocol(vendor, protocol), protocol).toBe(true)
      expect(protocolFor(vendor, protocol)).toBe(protocol)
    }
  })
})

describe('default base URLs', () => {
  it.each([
    ['generic', 'chat', ''],
    ['anthropic', 'anthropic', 'https://api.anthropic.com'],
    ['openai', 'responses', 'https://api.openai.com/v1'],
    ['deepseek', 'chat', 'https://api.deepseek.com'],
    ['deepseek', 'responses', 'https://api.deepseek.com'],
    ['deepseek', 'anthropic', 'https://api.deepseek.com/anthropic'],
    ['ark', 'anthropic', 'https://ark.cn-beijing.volces.com/api/compatible'],
    ['byteplus', 'responses', 'https://ark.ap-southeast.bytepluses.com/api/v3'],
    ['newcomer', 'chat', ''],
  ] as const)('%s + %s → %s', (vendor, protocol, url) => {
    expect(defaultBaseUrl(vendor as Vendor, protocol)).toBe(url)
  })

  it('recognizes the default with or without a trailing slash', () => {
    const openai = { vendor: 'openai', protocol: 'chat' } as const
    expect(
      usesDefaultBaseUrl({ ...openai, base_url: 'https://api.openai.com/v1/' }),
    ).toBe(true)
    expect(
      usesDefaultBaseUrl({ ...openai, base_url: 'https://llm.example.com' }),
    ).toBe(false)
    expect(
      usesDefaultBaseUrl({ ...openai, base_url: '', vendor: 'generic' }),
    ).toBe(true)
  })

  it('fills the new default only over an empty or default URL', () => {
    const generic = { vendor: 'generic', protocol: 'chat' } as const
    expect(
      baseUrlAfterSwitch({ ...generic, base_url: '' }, 'openai', 'chat'),
    ).toBe('https://api.openai.com/v1')
    const deepseek = {
      vendor: 'deepseek',
      protocol: 'chat',
      base_url: 'https://api.deepseek.com',
    } as const
    expect(baseUrlAfterSwitch(deepseek, 'deepseek', 'anthropic')).toBe(
      'https://api.deepseek.com/anthropic',
    )
    expect(baseUrlAfterSwitch(deepseek, 'generic', 'chat')).toBe('')
    const custom = { ...deepseek, base_url: 'https://proxy.example.com' }
    expect(baseUrlAfterSwitch(custom, 'byteplus', 'chat')).toBe(
      'https://proxy.example.com',
    )
  })
})

describe('base URLs', () => {
  it.each([
    ['https://api.openai.com/v1', true],
    ['http://10.0.0.5:8000', true],
    ['ftp://example.com', false],
    ['https://user:pass@example.com', false],
    ['https://example.com/?a=1', false],
    ['https://example.com/?', false],
    ['https://example.com/#top', false],
    ['example.com', false],
  ])('%s → %s', (url, valid) => {
    expect(isValidBaseUrl(url)).toBe(valid)
  })

  it.each(urlVectors)(
    'joins the shared URL vector: $name',
    ({ base_url, protocol, path, expected }) => {
      expect(upstreamUrl(base_url, protocol as Protocol, path)).toBe(expected)
    },
  )

  it.each([
    ['anthropic', '/v1/messages'],
    ['chat', '/v1/chat/completions'],
    ['responses', '/v1/responses'],
  ] as const)('can use the exact URL for %s', (protocol, path) => {
    expect(
      upstreamUrl(
        'https://api.example.com/custom/endpoint/',
        protocol,
        path,
        false,
      ),
    ).toBe('https://api.example.com/custom/endpoint')
  })

  it('previews the protocol endpoint once the URL is valid', () => {
    expect(endpointPreview(input({ protocol: 'responses' }))).toBe(
      'https://api.example.com/v1/responses',
    )
    expect(
      endpointPreview(
        input({
          protocol: 'responses',
          base_url: 'https://api.example.com/custom/endpoint',
          append_endpoint_path: false,
        }),
      ),
    ).toBe('https://api.example.com/custom/endpoint')
    expect(endpointPreview(input({ base_url: 'api.example' }))).toBe('')
  })
})

describe('validateUpstream', () => {
  it('accepts a complete upstream', () => {
    expect(validateUpstream(input())).toEqual({})
  })

  it('needs a key for a new managed upstream but keeps a stored one', () => {
    expect(validateUpstream(input({ api_key: '' })).api_key?.key).toBe(
      'validation.apiKeyRequired',
    )
    expect(
      validateUpstream(
        input({ api_key: '', base_url: stored.base_url }),
        stored,
      ),
    ).toEqual({})
    // A new address drops the stored key, so a new one is required.
    expect(validateUpstream(input({ api_key: '' }), stored).api_key?.key).toBe(
      'validation.apiKeyRequired',
    )
    expect(
      validateUpstream(input({ api_key: '', auth_mode: 'passthrough' })),
    ).toEqual({})
    expect(
      validateUpstream(input({ api_key: 'sk-ant-oat01-x' })).api_key?.key,
    ).toBe('validation.subscriptionKey')
  })

  it('checks headers, aliases and context windows', () => {
    const header = (headers: Record<string, string>) =>
      validateUpstream(input({ headers, base_url: stored.base_url }), stored)
        .headers
    expect(header({ 'OpenAI-Organization': '' })).toBeUndefined()
    // Stored values are dropped with a new address.
    expect(
      validateUpstream(
        input({ headers: { 'OpenAI-Organization': '' } }),
        stored,
      ).headers?.key,
    ).toBe('validation.headerValue')
    expect(header({ 'X-New': '' })).toEqual({
      key: 'validation.headerValue',
      values: { name: 'X-New' },
    })
    expect(header({ Host: 'x' })?.key).toBe('validation.headerReserved')
    expect(header({ 'X-Bad': 'a\nb' })?.key).toBe('validation.headerInvalid')
    expect(
      validateUpstream(input({ aliases: { fast: ' ' } })).aliases?.key,
    ).toBe('validation.aliasTarget')
    expect(
      validateUpstream(input({ context_windows: { m: 512 } })).context_windows,
    ).toEqual({
      key: 'validation.contextWindow',
      values: { name: 'm', min: 1024 },
    })
  })

  it('rejects a protocol the provider does not offer', () => {
    expect(
      validateUpstream(input({ vendor: 'openai', protocol: 'anthropic' }))
        .protocol,
    ).toEqual({
      key: 'validation.protocolUnsupported',
      values: { vendor: 'openai', protocol: 'anthropic' },
    })
    expect(
      validateUpstream(
        input({
          vendor: 'deepseek',
          protocol: 'anthropic',
          base_url: 'https://api.deepseek.com/anthropic',
        }),
      ),
    ).toEqual({})
  })

  it('checks name, URL and numbers', () => {
    const errors = validateUpstream(
      input({
        name: '',
        base_url: 'not a url',
        priority: 1.5,
        cache_min_tokens: -1,
      }),
    )
    expect(Object.keys(errors).sort()).toEqual([
      'base_url',
      'cache_min_tokens',
      'name',
      'priority',
    ])
  })
})

describe('replaysReasoning', () => {
  it('follows the vendor until the upstream overrides it', () => {
    expect(
      replaysReasoning({ vendor: 'deepseek', replay_reasoning: null }),
    ).toBe(true)
    expect(replaysReasoning({ vendor: 'ark', replay_reasoning: null })).toBe(
      true,
    )
    expect(
      replaysReasoning({ vendor: 'byteplus', replay_reasoning: null }),
    ).toBe(true)
    expect(replaysReasoning({ vendor: 'openai', replay_reasoning: null })).toBe(
      false,
    )
    expect(
      replaysReasoning({ vendor: 'generic', replay_reasoning: true }),
    ).toBe(true)
    expect(
      replaysReasoning({ vendor: 'deepseek', replay_reasoning: false }),
    ).toBe(false)
  })

  it('stores null while the switch matches the vendor default', () => {
    expect(replayReasoningSetting('deepseek', true)).toBeNull()
    expect(replayReasoningSetting('deepseek', false)).toBe(false)
    expect(replayReasoningSetting('generic', false)).toBeNull()
    expect(replayReasoningSetting('generic', true)).toBe(true)
  })
})

describe('URL corrections and changed secrets', () => {
  it('preserves custom hosts when adding a provider path', () => {
    expect(
      baseUrlCorrection(
        input({ vendor: 'openai', base_url: 'https://proxy.example/' }),
      ),
    ).toEqual({ reason: 'missingPath', url: 'https://proxy.example/v1' })
    expect(
      baseUrlCorrection(
        input({ vendor: 'ark', base_url: 'https://ark.example' }),
      ),
    ).toEqual({ reason: 'missingPath', url: 'https://ark.example/api/v3' })
    expect(
      baseUrlCorrection(
        input({ vendor: 'generic', base_url: 'https://proxy.example' }),
      ),
    ).toBeUndefined()
  })
  it('rejects Anthropic bases with /v1 and suggests removing it', () => {
    const draft = input({
      protocol: 'anthropic',
      base_url: 'https://proxy.example/anthropic/v1/',
    })
    expect(baseUrlCorrection(draft)).toEqual({
      reason: 'anthropicVersion',
      url: 'https://proxy.example/anthropic',
    })
    expect(validateUpstream(draft).base_url?.key).toBe(
      'validation.anthropicBaseUrl',
    )
    const exact = { ...draft, append_endpoint_path: false }
    expect(baseUrlCorrection(exact)).toBeUndefined()
    expect(validateUpstream(exact)).toEqual({})
  })
  it('recognizes address changes but ignores trailing slashes', () => {
    expect(baseUrlChanged({ base_url: stored.base_url + '/' }, stored)).toBe(
      false,
    )
    expect(baseUrlChanged({ base_url: 'https://other.example' }, stored)).toBe(
      true,
    )
  })
  it('reads redacted upstream details without header names', () => {
    expect(
      toUpstreamInput({ ...stored, header_names: undefined }).headers,
    ).toEqual({})
  })
})
