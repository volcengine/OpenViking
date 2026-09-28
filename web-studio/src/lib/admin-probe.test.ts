import { describe, expect, it } from 'vitest'
import { createServer } from 'node:http'
import { once } from 'node:events'
import { probeStudioConnection } from './admin'

describe('trusted connection probes', () => {
  it.each([false, true, undefined])(
    'uses explicit Root key requirement metadata: %s',
    async (required) => {
      const requests: string[] = []
      const server = createServer((req, res) => {
        requests.push(req.url || '')
        res.setHeader('Content-Type', 'application/json')
        if (req.url === '/health') {
          res.end(
            JSON.stringify({
              auth_mode: 'trusted',
              root_api_key_required: required,
            }),
          )
        } else if (req.url?.startsWith('/api/v1/fs/ls')) {
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
          apiKey: '',
          adminApiKey: '',
        })
        expect(result.rootApiKeyRequired).toBe(required)
        expect(result.admin).toMatchObject({
          state: 'skipped',
          detailCode: 'controlKeyRequired',
        })
        expect(result.data).toMatchObject({
          state: 'error',
          statusCode: 403,
          errorCode: 'PERMISSION_DENIED',
        })
        expect(requests.some((path) => path.startsWith('/api/v1/admin'))).toBe(
          false,
        )
      } finally {
        await new Promise<void>((resolve) => server.close(() => resolve()))
      }
    },
  )
})
