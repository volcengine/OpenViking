/**
 * In-process OpenViking server stub for keyless e2e runs. Implements the
 * endpoints the plugin and the shared recall runtime call, in the real
 * response envelope (`{ status, result }`), and records every request.
 *
 * The context-face endpoints (`search/search`, `search/recall`) return 404 so
 * the shared `buildRecallBlock` deterministically exercises its raw
 * `search/find` fallback.
 */

import { createServer } from 'node:http'
import type { Server } from 'node:http'

/** One recorded request. */
export interface StubRequest {
  method: string
  path: string
  body: unknown
}

/** A running stub with its base URL and recorded traffic. */
export interface OvStub {
  baseUrl: string
  requests: StubRequest[]
  close: () => Promise<void>
}

const FIND_RESULT = {
  memories: [{
    uri: 'viking://memory/dsh-e2e-note',
    score: 0.92,
    abstract: 'The dsh e2e user prefers concise answers.',
    level: 0,
  }],
  resources: [],
  skills: [],
}

function envelope(result: unknown): string {
  return JSON.stringify({ status: 'ok', result })
}

/** Start the stub on an ephemeral loopback port. */
export async function startOvStub(): Promise<OvStub> {
  const requests: StubRequest[] = []
  const server: Server = createServer((request, response) => {
    const chunks: Buffer[] = []
    request.on('data', chunk => chunks.push(chunk as Buffer))
    request.on('end', () => {
      const raw = Buffer.concat(chunks).toString('utf8')
      let body: unknown
      try {
        body = raw === '' ? undefined : JSON.parse(raw)
      } catch {
        body = raw
      }
      const path = (request.url ?? '').split('?')[0] ?? ''
      requests.push({ method: request.method ?? '', path, body })
      response.setHeader('content-type', 'application/json')

      if (path === '/health') {
        response.end(JSON.stringify({ status: 'ok' }))
        return
      }
      // Force the shared recall runtime onto its raw find() fallback.
      if (path === '/api/v1/search/search' || path === '/api/v1/search/recall') {
        response.statusCode = 404
        response.end(JSON.stringify({ status: 'error', error: { message: 'not found' } }))
        return
      }
      if (path === '/api/v1/search/find') {
        response.end(envelope(FIND_RESULT))
        return
      }
      if (path === '/api/v1/system/status') {
        response.end(envelope({ user: 'e2e' }))
        return
      }
      if (path === '/api/v1/fs/ls') {
        response.end(envelope([]))
        return
      }
      if (path === '/api/v1/content/read') {
        response.end(envelope('stub content'))
        return
      }
      if (path === '/api/v1/sessions' && request.method === 'POST') {
        response.end(envelope({ session_id: `stub-${requests.length}` }))
        return
      }
      if (path.startsWith('/api/v1/sessions/')) {
        if (path.endsWith('/context')) {
          response.end(envelope({ latest_archive_overview: null }))
          return
        }
        response.end(envelope({ session_id: path.split('/')[4], ok: true }))
        return
      }
      response.statusCode = 404
      response.end(JSON.stringify({ status: 'error', error: { message: `no stub for ${path}` } }))
    })
  })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  const address = server.address()
  if (address === null || typeof address === 'string') throw new Error('stub failed to bind')
  return {
    baseUrl: `http://127.0.0.1:${address.port}`,
    requests,
    close: () => new Promise((resolve, reject) => {
      server.close(error => error ? reject(error) : resolve())
    }),
  }
}
