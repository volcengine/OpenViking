// Browser-test daemon. This is not mounted by the production web server.
import http from 'node:http'
import { readFile } from 'node:fs/promises'

const fixtures = JSON.parse(await readFile(new URL('./fixtures.json', import.meta.url), 'utf8'))
const json = (response, body, status = 200) => {
  response.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' })
  response.end(JSON.stringify(body))
}

export function startFixtureDaemon(port = 0) {
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost')
    const path = url.pathname
    if (path === '/api/v1/capabilities') return json(response, fixtures.capabilities)
    if (path === '/health') return json(response, {
      status: 'degraded', subscriber: 'degraded', activeTurns: 2,
      connectors: { lark: { status: 'running', running: true }, email: { status: 'stopped', running: false, error: 'Authentication expired' } },
    })
    if (path === '/api/activity') return json(response, {
      generatedAt: Date.parse(fixtures.overview.generatedAt),
      service: { ready: true, subscriber: 'degraded', activeTurns: 2 },
      overview: { attention: 1, receivedToday: 18, sentToday: 16 },
      sessions: fixtures.sessions.items.map((session) => ({ epoch: session.epoch, startMs: Date.parse(session.startedAt), endMs: session.endedAt ? Date.parse(session.endedAt) : null, activityCount: session.activityCount })),
      activities: fixtures.activities.items.map((activity) => ({ ...activity, id: Number(activity.id), createdAtMs: Date.parse(activity.createdAt) })),
      nextCursor: '1000', hasNewer: false,
    })
    if (path.endsWith('/overview')) return json(response, fixtures.overview)
    if (path.endsWith('/metrics')) return json(response, fixtures.metrics)
    if (path.endsWith('/sessions')) return json(response, fixtures.sessions)
    if (path.endsWith('/activities')) return json(response, fixtures.activities)
    if (path.endsWith('/tasks')) {
      const status = url.searchParams.get('status')
      const runtime = url.searchParams.get('runtime')
      const query = url.searchParams.get('q')?.toLowerCase()
      const items = fixtures.tasks.filter((task) => (!status || task.status === status)
        && (!runtime || task.runtime === runtime)
        && (!query || task.title.toLowerCase().includes(query) || task.id.includes(query)))
      return json(response, { items, nextCursor: null, generatedAt: fixtures.overview.generatedAt })
    }
    const detail = /\/tasks\/([^/]+)$/.exec(path)
    if (detail) {
      const task = fixtures.tasks.find((item) => item.id === detail[1])
      return task ? json(response, task) : json(response, { error: { code: 'not_found', message: 'Task not found' } }, 404)
    }
    if (path.endsWith('/inputs')) return json(response, fixtures.inputs)
    if (path.endsWith('/reports')) {
      const reports = path.includes('/t198/') ? { ...fixtures.reports, items: [fixtures.reports.items[1]] } : fixtures.reports
      return json(response, reports)
    }
    if (path.endsWith('/events')) return json(response, fixtures.events)
    return json(response, { error: { code: 'not_found', message: `No fixture for ${path}` } }, 404)
  })
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server)))
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  const server = await startFixtureDaemon(Number(process.env.PORT || 3740))
  console.log(`RFC fixture daemon listening on ${server.address().port}; test data only`)
}
