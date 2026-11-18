import fixtures from '../../test/fixtures.json'
import { vi } from 'vitest'

export function installFixtureFetch() {
  return vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(input instanceof Request ? input.url : String(input))
    const path = url.pathname
    const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
    if (path === '/api/v1/instances') return response(fixtures.instances)
    if (path === '/api/v1/capabilities') return response(fixtures.capabilities)
    if (path.endsWith('/overview')) return response(fixtures.overview)
    if (path.endsWith('/metrics')) return response(fixtures.metrics)
    if (path.endsWith('/sessions')) return response(fixtures.sessions)
    if (path.endsWith('/activities')) return response(fixtures.activities)
    if (path.endsWith('/tasks')) return response({ items: fixtures.tasks, nextCursor: null })
    if (path.endsWith('/inputs')) return response(fixtures.inputs)
    if (path.endsWith('/reports')) return response(fixtures.reports)
    if (path.endsWith('/events')) return response(fixtures.events)
    const detail = /\/tasks\/([^/]+)$/.exec(path)
    if (detail) return response(fixtures.tasks.find((task) => task.id === detail[1]))
    return response({ error: { code: 'not_found', message: `No fixture for ${path}` } }, 404)
  })
}
