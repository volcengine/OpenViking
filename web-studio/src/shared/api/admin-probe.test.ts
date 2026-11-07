import { probeStudioConnection } from '#/shared/api/admin'
import { once } from 'node:events'
import { createServer } from 'node:http'
import { describe, expect, it } from 'vitest'

describe('management role probes', () => {
  it.each([
    ['root', { role: 'root' }, 'ok', 'rootAvailable'],
    [
      'account admin',
      { role: 'admin', account_id: 'acme' },
      'ok',
      'accountAdminAvailable',
    ],
    [
      'other account admin',
      { role: 'admin', account_id: 'other' },
      'error',
      undefined,
    ],
    ['ordinary user', { role: 'user', account_id: 'acme' }, 'error', undefined],
    ['missing identity', { healthy: true }, 'error', undefined],
    ['missing admin account', { role: 'admin' }, 'error', undefined],
    [
      'invalid credential',
      { error: { code: 'UNAUTHENTICATED' } },
      'error',
      undefined,
    ],
  ] as const)(
    'uses authenticated health identity: %s',
    async (name, health, state, detailCode) => {
      const requests: Array<[string, string | undefined]> = []
      const server = createServer((req, res) => {
        requests.push([
          req.url || '',
          req.headers['x-api-key'] as string | undefined,
        ])
        res.setHeader('Content-Type', 'application/json')
        res.statusCode = name === 'invalid credential' ? 401 : 200
        res.end(JSON.stringify(req.url === '/health' ? health : { result: [] }))
      })
      server.listen(0, '127.0.0.1')
      await once(server, 'listening')
      const { port } = server.address() as { port: number }
      try {
        const result = await probeStudioConnection({
          baseUrl: `http://127.0.0.1:${port}`,
          serverMode: 'api_key',
          accountId: 'acme',
          userId: 'alice',
          apiKey: '',
          adminApiKey: 'control-key',
        })
        // detailCode only exists on the 'ok' results per CapabilityProbeResult;
        // an explicit undefined key would make toMatchObject require it.
        expect(result.admin).toMatchObject(
          detailCode === undefined ? { state } : { state, detailCode },
        )
        if (state === 'error') {
          expect(result.admin).not.toHaveProperty('detailCode')
          expect(result.admin).toMatchObject(
            name === 'invalid credential'
              ? { statusCode: 401, errorCode: 'UNAUTHENTICATED' }
              : { statusCode: 403, errorCode: 'PERMISSION_DENIED' },
          )
        }
        expect(requests).toEqual([['/health', 'control-key']])
      } finally {
        await new Promise<void>((resolve) => server.close(() => resolve()))
      }
    },
  )
})

describe('trusted connection probes', () => {
  it.each(['', 'root-key'])(
    'requires a key for trusted data access and preserves server errors: %s',
    async (apiKey) => {
      const requests: Array<[string, string | undefined]> = []
      const server = createServer((req, res) => {
        requests.push([
          req.url || '',
          req.headers['x-api-key'] as string | undefined,
        ])
        res.setHeader('Content-Type', 'application/json')
        if (req.url?.startsWith('/api/v1/fs/ls')) {
          res.statusCode = 403
          res.end(
            JSON.stringify({
              error: {
                code: 'PERMISSION_DENIED',
                message: 'Data access denied',
              },
            }),
          )
        } else {
          res.statusCode = 400
          res.end(
            JSON.stringify({
              error: { code: 'INVALID_ARGUMENT', message: 'Missing identity' },
            }),
          )
        }
      })
      server.listen(0, '127.0.0.1')
      await once(server, 'listening')
      const { port } = server.address() as { port: number }
      try {
        const result = await probeStudioConnection({
          baseUrl: `http://127.0.0.1:${port}`,
          serverMode: 'trusted',
          accountId: 'account-a',
          userId: 'alice',
          apiKey,
          adminApiKey: '',
        })
        expect(result.admin).toMatchObject({
          state: 'skipped',
          detailCode: 'trustedKeyRequired',
        })
        if (apiKey) {
          expect(result.data).toMatchObject({
            state: 'error',
            statusCode: 403,
            errorCode: 'PERMISSION_DENIED',
          })
          expect(requests).toEqual([
            [expect.stringContaining('/api/v1/fs/ls'), apiKey],
          ])
        } else {
          expect(result.data).toMatchObject({
            state: 'skipped',
            detailCode: 'trustedKeyRequired',
          })
          expect(requests).toEqual([])
        }
      } finally {
        await new Promise<void>((resolve) => server.close(() => resolve()))
      }
    },
  )
})
