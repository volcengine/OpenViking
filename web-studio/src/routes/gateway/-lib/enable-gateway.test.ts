import { describe, expect, it } from 'vitest'

import {
  enableGatewayContent,
  generateManagementKey,
  isRevisionConflict,
} from './enable-gateway'

const KEY = 'ovgm_0123456789abcdefghijklmnopqrstuvwxyzABCDEFG'

describe('enableGatewayContent', () => {
  it('keeps every other ov.conf field and the existing gateway fields', () => {
    const content = `{
  "server": { "host": "0.0.0.0", "port": 1933, "root_api_key": "root" },
  "embedding": {
    "dense": { "provider": "openai", "model": "text-embedding-3-small", "dimension": 1536 }
  },
  "gateway": {
    "enabled": false,
    "public_url": "https://ov.example.com/gateway",
    "log_retention_days": 7
  },
  "vlm": { "provider": "openai", "model": "gpt-4o" }
}
`
    const updated = enableGatewayContent(content, KEY)
    const before = JSON.parse(content)
    const after = JSON.parse(updated)
    expect(after).toEqual({
      ...before,
      gateway: {
        ...before.gateway,
        enabled: true,
        management_key: KEY,
      },
    })
    // Only the gateway section changes; the rest of the text stays byte for byte.
    expect(
      updated.startsWith(content.slice(0, content.indexOf('"gateway"'))),
    ).toBe(true)
    expect(updated.endsWith(content.slice(content.indexOf('  "vlm"')))).toBe(
      true,
    )
    expect(updated).toContain(
      `    "log_retention_days": 7,\n    "management_key": "${KEY}"\n  },`,
    )
  })

  it('adds a gateway section after the last member', () => {
    const content = '{\n  "server": {"port": 1933}\n}\n'
    expect(enableGatewayContent(content)).toBe(
      '{\n  "server": {"port": 1933},\n  "gateway": {\n    "enabled": true\n  }\n}\n',
    )
  })

  it('keeps unquoted environment references and replaces a duplicate gateway in place', () => {
    const content =
      '{"embedding": {"dense": {"dimension": $DIM, "api_key": "${OPENAI_KEY}"}}, "gateway": {"enabled": false}, "gateway": {"storage_path": "/data", "enabled": false}}'
    expect(enableGatewayContent(content)).toBe(
      '{"embedding": {"dense": {"dimension": $DIM, "api_key": "${OPENAI_KEY}"}}, "gateway": {"enabled": false}, "gateway": {"storage_path": "/data", "enabled": true}}',
    )
  })

  it('fills an empty or non-object gateway section', () => {
    expect(JSON.parse(enableGatewayContent('{"gateway": {}}'))).toEqual({
      gateway: { enabled: true },
    })
    expect(JSON.parse(enableGatewayContent('{"gateway": null}', KEY))).toEqual({
      gateway: { enabled: true, management_key: KEY },
    })
    expect(JSON.parse(enableGatewayContent('{}'))).toEqual({
      gateway: { enabled: true },
    })
  })

  it('keeps a leading byte order mark', () => {
    expect(enableGatewayContent('\uFEFF{"server": {}}')).toBe(
      '\uFEFF{"server": {}, "gateway": {"enabled": true}}',
    )
  })

  it('refuses content that is not a JSON object', () => {
    expect(() => enableGatewayContent('[]')).toThrow()
  })
})

it('generates a management key in the init format', () => {
  const key = generateManagementKey()
  expect(key).toMatch(/^ovgm_[A-Za-z0-9_-]{43}$/)
  expect(generateManagementKey()).not.toBe(key)
})

it('recognizes revision conflicts from the configuration API', () => {
  expect(
    isRevisionConflict(new Error('ov.conf changed; reload before saving')),
  ).toBe(true)
  expect(isRevisionConflict(new Error('ov.conf is read-only'))).toBe(false)
})
