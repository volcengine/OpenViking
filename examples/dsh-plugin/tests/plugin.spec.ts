import { createServer } from 'node:http'
import type { AddressInfo } from 'node:net'
import { afterEach, describe, expect, it } from 'vitest'
import { OVClient } from '../src/client.ts'
import { buildRuntimeCfg } from '../src/config.ts'
import { CaptureSync } from '../src/sync.ts'

const savedEnv = { ...process.env }

afterEach(() => {
  for (const key of Object.keys(process.env)) {
    if (!(key in savedEnv)) delete process.env[key]
  }
  Object.assign(process.env, savedEnv)
})

function isolateCredentials(): void {
  process.env.OPENVIKING_CLI_CONFIG_FILE = '/nonexistent/ovcli.conf'
  process.env.OPENVIKING_CONFIG_FILE = '/nonexistent/ov.conf'
  delete process.env.OPENVIKING_CREDENTIAL_SOURCE
  delete process.env.OPENVIKING_CREDENTIALS_SOURCE
  delete process.env.OPENVIKING_BASE_URL
  delete process.env.OPENVIKING_URL
  delete process.env.OPENVIKING_MCP_URL
  delete process.env.OPENVIKING_API_KEY
  delete process.env.OPENVIKING_BEARER_TOKEN
}

describe('buildRuntimeCfg', () => {
  it('prefers plugin baseUrl, then env, and derives a workspace peer', () => {
    isolateCredentials()
    process.env.OPENVIKING_BASE_URL = 'http://env-server:1933'
    const fromEnv = buildRuntimeCfg({}, '0.0.0')
    expect(fromEnv?.baseUrl).toBe('http://env-server:1933')
    expect(fromEnv?.peerId).toBe(process.cwd().replace(/[^A-Za-z0-9]/g, '-'))

    const explicit = buildRuntimeCfg({ baseUrl: 'http://explicit/' }, '0.0.0')
    expect(explicit?.baseUrl).toBe('http://explicit')

    const noPeer = buildRuntimeCfg({ workspacePeer: false }, '0.0.0')
    expect(noPeer?.peerId).toBe('')
  })

  it('falls back to the family default local server when unconfigured', () => {
    isolateCredentials()
    const cfg = buildRuntimeCfg({}, '0.0.0')
    // credentials.mjs resolves ov.conf defaults to a local server rather
    // than "nothing" — the plugin then health-checks lazily.
    expect(cfg?.baseUrl).toBe('http://127.0.0.1:1933')
  })
})

describe('CaptureSync.ovSessionId', () => {
  it('derives the family harness session id', () => {
    expect(CaptureSync.ovSessionId('abc-123')).toBe('dsh-abc-123')
  })
})

describe('OVClient.find', () => {
  it('flattens buckets, tolerates junk, and sorts by score', async () => {
    const server = createServer((_request, response) => {
      response.setHeader('content-type', 'application/json')
      response.end(JSON.stringify({ status: 'ok', result: {
        memories: [
          { uri: 'viking://a', score: 0.4, abstract: 'A' },
          { score: 1 },
          null,
        ],
        resources: [{ uri: 'viking://b', score: 0.9 }],
      } }))
    })
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
    const port = (server.address() as AddressInfo).port
    try {
      isolateCredentials()
      process.env.OPENVIKING_BASE_URL = `http://127.0.0.1:${port}`
      const cfg = buildRuntimeCfg({}, '0.0.0')
      expect(cfg).toBeDefined()
      const client = new OVClient(cfg!)
      const items = await client.find('anything', 5)
      expect(items.map(item => item.uri)).toEqual(['viking://b', 'viking://a'])
      expect(items[0]!.abstract).toBe('')
      expect(items[1]!.abstract).toBe('A')
    } finally {
      await new Promise(resolve => server.close(resolve))
    }
  })
})
