import { describe, expect, it } from 'vitest'
import { createServer } from 'node:http'
import { once } from 'node:events'
import { probeStudioConnection } from './admin'

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
