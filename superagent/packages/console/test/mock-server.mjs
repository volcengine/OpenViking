// Isolated RFC contract demo. Never import this server into production code.
import { createHash, randomUUID } from 'node:crypto'
import { readFile } from 'node:fs/promises'
import http from 'node:http'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const packageDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const initialFixtures = JSON.parse(await readFile(new URL('./fixtures.json', import.meta.url), 'utf8'))
const now = () => new Date().toISOString()
const clone = (value) => structuredClone(value)
const errorBody = (code, message, details = {}, extra = {}) => ({ error: { code, message, retryable: false, details, operationId: null, uncertain: false, ...extra } })

function operation(kind, instanceId, names = ['validate', 'save', 'restart', 'verify']) {
  const timestamp = now()
  return { id: `op_${randomUUID().replaceAll('-', '').slice(0, 16)}`, kind, instanceId, state: 'succeeded', steps: names.map((name) => ({ name, state: 'succeeded' })), createdAt: timestamp, updatedAt: timestamp }
}

export function createMockState() {
  const fixtures = clone(initialFixtures)
  for (const key of ['tasks.control', 'runtime.control', 'sessions.control', 'configuration.read', 'configuration.write', 'instances.setup', 'instances.process', 'providers.read', 'providers.write', 'logs.read', 'diagnostics.bundle', 'audit.read']) {
    fixtures.capabilities.modules[key] = { enabled: true, available: true, effective: true, reasonCode: null }
  }
  return {
    ...fixtures,
    configuration: {
      instanceId: 'primary', revision: 'sha256:6e4d3f5201', restartRequired: false, ignoredFields: [],
      desired: { displayName: 'Primary Agent', workspace: '/home/demo/work/primary', runtime: 'pi', model: 'volcengine/deepseek-v3.2', effort: 'medium', scheduler: 'workers', connectors: ['lark', 'email'] },
      effective: { runtime: 'pi', model: 'volcengine/deepseek-v3.2', ready: true },
      capabilities: { canEdit: true, canRestart: true, workerMode: true },
    },
    operations: [operation('instance.restart', 'primary')],
    providers: [
      { id: 'ark-main', revision: 'sha256:provider01', protocol: 'openai-compatible', baseUrl: 'https://ark.cn-beijing.volces.com/api/v3', models: [{ id: 'volcengine/deepseek-v3.2', efforts: ['medium', 'high'] }, { id: 'demo/model-small', efforts: ['low', 'medium'] }], credential: { ref: 'cred_ark_main', configured: true, configuredAt: '2026-11-14T12:00:00Z' }, authKind: 'api_key', bindings: [{ instanceId: 'primary', runtime: 'pi', state: 'synced' }, { instanceId: 'primary', runtime: 'dsh', state: 'restart_required' }] },
      { id: 'claude-oauth', revision: 'sha256:provider02', protocol: 'anthropic', baseUrl: 'https://api.anthropic.com', models: [{ id: 'claude-sonnet-demo', efforts: ['medium', 'high'] }], credential: { configured: true }, authKind: 'oauth', bindings: [{ instanceId: 'primary', runtime: 'claude', state: 'synced' }] },
    ],
    logs: [
      { id: 'log_3', stream: 'daemon', severity: 'error', message: 'Email connector authentication expired; reconnect from the CLI', createdAt: '2026-11-14T15:04:00Z' },
      { id: 'log_2', stream: 'daemon', severity: 'info', message: 'Worker slot assigned to task t203 turn 1', createdAt: '2026-11-14T15:03:30Z' },
      { id: 'log_1', stream: 'web', severity: 'info', message: 'Shared Web ready on loopback; host allowlist enforced', createdAt: '2026-11-14T15:00:00Z' },
    ],
    audit: [{ id: 'audit_1', actor: 'owner@mock', action: 'instance.restart', instanceId: 'primary', result: 'succeeded', revision: 'sha256:6e4d3f5201', createdAt: '2026-11-14T14:00:00Z' }],
    idempotency: new Map(),
  }
}

function checkQuery(url, allowed) {
  const accepted = new Set(['instance', ...allowed])
  for (const key of new Set(url.searchParams.keys())) if (!accepted.has(key)) return `Unknown query parameter: ${key}`
  for (const key of accepted) if (url.searchParams.getAll(key).length > 1) return `Repeated query parameter: ${key}`
  const limit = url.searchParams.get('limit')
  if (limit !== null && (!/^\d+$/.test(limit) || Number(limit) < 1 || Number(limit) > 100)) return 'limit must be an integer from 1 to 100'
  if (url.searchParams.has('before') && url.searchParams.has('after')) return 'before and after are mutually exclusive'
  return null
}

async function bodyOf(request) {
  const chunks = []
  let size = 0
  for await (const chunk of request) {
    size += chunk.length
    if (size > 64 * 1024) throw Object.assign(new Error('Request body too large'), { status: 413, code: 'invalid_argument' })
    chunks.push(chunk)
  }
  if (!chunks.length) return {}
  try { return JSON.parse(Buffer.concat(chunks).toString('utf8')) }
  catch { throw Object.assign(new Error('Invalid JSON body'), { status: 400, code: 'invalid_argument' }) }
}

export async function startMockServer({ port = 0, host = '127.0.0.1', publicDir = path.resolve(packageDir, '../web/public'), quiet = true } = {}) {
  const state = createMockState()
  const server = http.createServer(async (request, response) => {
    let json = (body, status = 200, headers = {}) => {
      response.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', ...headers })
      response.end(JSON.stringify(body))
    }
    try {
      const url = new URL(request.url, `http://${request.headers.host}`)
      const pathname = url.pathname
      if (!['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)) return json(errorBody('forbidden', 'Loopback host required'), 403)
      if (request.headers.origin) {
        const origin = new URL(request.headers.origin)
        if (origin.host !== request.headers.host || origin.protocol !== 'http:') return json(errorBody('forbidden', 'Cross-origin request rejected'), 403)
      }
      if (pathname === '/' || pathname === '/console') {
        response.writeHead(302, { location: `/console/${url.search}` }); return response.end()
      }
      if (!pathname.startsWith('/api/')) {
        const asset = pathname === '/console/' ? 'console/index.html' : pathname.replace(/^\//, '')
        if (!/^console\/(?:index\.html|assets\/[a-zA-Z0-9._-]+\.(?:js|css))$/.test(asset)) return json(errorBody('not_found', 'Not found'), 404)
        const content = await readFile(path.join(publicDir, asset))
        response.writeHead(200, { 'content-type': asset.endsWith('.html') ? 'text/html; charset=utf-8' : asset.endsWith('.css') ? 'text/css; charset=utf-8' : 'text/javascript; charset=utf-8', 'cache-control': 'no-store', 'content-security-policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'" })
        return response.end(content)
      }
      if (request.method !== 'GET' && request.headers['x-csrf-token'] !== 'demo-owner') return json(errorBody('forbidden', 'A same-origin CSRF header is required'), 403)
      if (request.headers['x-demo-role'] === 'viewer' && (request.method !== 'GET' || /\/(?:inputs|reports|configuration|logs)(?:\/|$)/.test(pathname))) return json(errorBody('permission_denied', 'Viewer cannot read protected content or mutate state'), 403)
      const body = request.method === 'GET' ? {} : await bodyOf(request)
      const instance = url.searchParams.get('instance') || 'primary'
      const audit = (action, result = 'succeeded', revision) => state.audit.unshift({ id: `audit_${randomUUID().slice(0, 8)}`, actor: 'owner@mock', action, instanceId: instance, result, revision, createdAt: now() })
      const idem = request.headers['idempotency-key']
      if (request.method !== 'GET' && idem) {
        const hash = createHash('sha256').update(`${request.method}:${pathname}:${JSON.stringify(body)}`).digest('hex')
        const previous = state.idempotency.get(idem)
        if (previous && previous.hash !== hash) return json(errorBody('invalid_state', 'Idempotency-Key was already used with a different body'), 409)
        if (previous) return json(previous.body, previous.status)
        const end = json
        json = (payload, status = 200, headers = {}) => { state.idempotency.set(idem, { hash, body: payload, status }); return end(payload, status, headers) }
      }

      if (pathname === '/api/v1/instances' && request.method === 'GET') return json(state.instances)
      if (pathname === '/api/v1/instances' && request.method === 'POST') {
        if (!/^[a-z][a-z0-9-]{1,30}$/.test(body.id)) return json(errorBody('validation_failed', 'Invalid instance id'), 422)
        const op = operation('instance.setup', body.id, ['validate', 'create', 'configure', 'verify']); state.operations.unshift(op); audit('instance.setup', 'succeeded', op.id); return json(op, 202)
      }
      if (pathname === '/api/v1/capabilities') return json({ ...state.capabilities, instanceId: instance, generatedAt: now() })
      if (pathname === '/api/v1/setup/runtimes') return json({ items: [{ id: 'pi', installed: true, authenticated: true, scheduler: true }, { id: 'dsh', installed: true, authenticated: true, scheduler: true }] })
      if (pathname === '/api/v1/operations') { const q = checkQuery(url, ['instanceId', 'state']); if (q) return json(errorBody('invalid_argument', q), 400); return json({ items: state.operations.filter((x) => (!url.searchParams.get('state') || x.state === url.searchParams.get('state'))), nextCursor: null, generatedAt: now() }) }
      const operationMatch = /^\/api\/v1\/operations\/([^/]+)$/.exec(pathname)
      if (operationMatch) { const op = state.operations.find((x) => x.id === operationMatch[1]); return op ? json(op) : json(errorBody('not_found', 'Operation not found'), 404) }
      if (pathname === '/api/v1/model-providers' && request.method === 'GET') return json({ items: state.providers, nextCursor: null, generatedAt: now() })
      if (pathname === '/api/v1/model-providers' && request.method === 'POST') {
        if (!body.id || !body.baseUrl || !Array.isArray(body.models)) return json(errorBody('validation_failed', 'id, baseUrl, and models are required'), 422)
        if (state.providers.some((x) => x.id === body.id)) return json(errorBody('invalid_state', 'Provider already exists'), 409)
        const provider = { id: body.id, revision: `sha256:${randomUUID().slice(0, 12)}`, protocol: body.protocol, baseUrl: body.baseUrl, models: body.models.map((id) => ({ id, efforts: ['medium'] })), credential: { configured: false }, authKind: 'api_key', bindings: [] }; state.providers.push(provider); audit('provider.create', 'succeeded', provider.revision); return json(provider, 201)
      }
      const providerMatch = /^\/api\/v1\/model-providers\/([^/]+)(?:\/actions\/validate|\/credential)?$/.exec(pathname)
      if (providerMatch) {
        const provider = state.providers.find((x) => x.id === providerMatch[1]); if (!provider) return json(errorBody('not_found', 'Provider not found'), 404)
        if (pathname.endsWith('/actions/validate')) return json({ valid: true, protocol: provider.protocol, authenticated: provider.credential.configured, models: provider.models.length })
        if (pathname.endsWith('/credential') && request.method === 'PUT') { if (typeof body.secret !== 'string' || body.secret.length < 4) return json(errorBody('validation_failed', 'A non-empty credential is required'), 422); provider.credential = { ref: `cred_${randomUUID().slice(0, 8)}`, configured: true, configuredAt: now() }; audit('provider.credential.rotate'); return json({ credentialRef: provider.credential.ref, configuredAt: provider.credential.configuredAt }) }
        if (request.method === 'PATCH') { if (request.headers['if-match'] !== provider.revision) return json(errorBody('revision_conflict', 'Provider changed; reload before saving', { currentRevision: provider.revision }), 412); provider.baseUrl = body.baseUrl; provider.revision = `sha256:${randomUUID().slice(0, 12)}`; audit('provider.update', 'succeeded', provider.revision); return json(provider) }
      }
      if (pathname === '/api/v1/audit') return json({ items: state.audit, nextCursor: null, generatedAt: now() })

      const prefix = `/api/v1/instances/${instance}`
      if (!pathname.startsWith(prefix)) return json(errorBody('not_found', 'API route not found'), 404)
      const suffix = pathname.slice(prefix.length)
      if (suffix === '/overview') return json({ ...state.overview, runtime: state.overview.runtime, generatedAt: now() })
      if (suffix === '/health') return json({ status: state.overview.health, ready: state.overview.ready, connectors: state.overview.connectors })
      if (suffix === '/runtime') return json(state.overview.runtime)
      if (suffix === '/runtime/models') { const q = checkQuery(url, ['refresh']); if (q) return json(errorBody('invalid_argument', q), 400); return json({ items: [{ id: 'volcengine/deepseek-v3.2', efforts: ['medium', 'high'] }, { id: 'demo/model-small', efforts: ['low', 'medium'] }], nextCursor: null }) }
      if (suffix === '/sessions') return json(state.sessions)
      if (suffix === '/activities') return json(state.activities)
      if (suffix === '/metrics') return json(state.metrics)
      if (suffix === '/tasks') {
        const q = checkQuery(url, ['status', 'runtime', 'q', 'before', 'after', 'limit']); if (q) return json(errorBody('invalid_argument', q), 400)
        const query = url.searchParams.get('q')?.toLowerCase(); const status = url.searchParams.get('status'); const runtime = url.searchParams.get('runtime')
        return json({ items: state.tasks.filter((task) => (!status || task.status === status) && (!runtime || task.runtime === runtime) && (!query || task.title.toLowerCase().includes(query) || task.id.includes(query))), nextCursor: null, generatedAt: now() })
      }
      const taskMatch = /^\/tasks\/([^/]+)(?:\/(inputs|reports|events|actions\/cancel))?$/.exec(suffix)
      if (taskMatch) {
        const task = state.tasks.find((x) => x.id === taskMatch[1]); if (!task) return json(errorBody('not_found', 'Task not found'), 404)
        if (taskMatch[2] === 'inputs') return json(state.inputs)
        if (taskMatch[2] === 'reports') return json({ ...state.reports, items: task.id === 't198' ? [state.reports.items[1]] : state.reports.items })
        if (taskMatch[2] === 'events') return json(state.events)
        if (taskMatch[2] === 'actions/cancel') { if (body.expectedRevision !== task.revision) return json(errorBody('revision_conflict', 'Task changed; reload before cancelling', { currentRevision: task.revision }), 412); if (task.status === 'stopped') return json({ taskId: task.id, status: 'stopped', revision: task.revision, worker: { stopRequested: true, termination: 'confirmed' } }); task.status = 'stopped'; task.revision += 1; task.worker = { ...task.worker, state: 'stopped', canCancel: false, stopRequested: true, termination: 'pending' }; task.updatedAt = now(); audit('task.cancel', 'succeeded', task.revision); return json({ taskId: task.id, status: 'stopped', revision: task.revision, worker: { stopRequested: true, termination: 'pending' } }) }
        return json(task)
      }
      if (suffix === '/sessions/actions/new') { const next = Math.max(...state.sessions.items.map((x) => x.epoch)) + 1; for (const session of state.sessions.items) session.current = false; const session = { epoch: next, startedAt: now(), endedAt: null, journalCount: 0, activityCount: 0, current: true }; state.sessions.items.push(session); state.overview.runtime.epoch = next; audit('session.new'); return json(session, 201) }
      if (suffix === '/runtime/actions/select' || suffix === '/runtime/actions/model' || suffix === '/runtime/actions/effort') {
        const runtime = state.overview.runtime
        if (body.expectedRevision !== undefined && body.expectedRevision !== runtime.revision) return json(errorBody('revision_conflict', 'Runtime changed; reload before transitioning', { currentRevision: runtime.revision }), 412)
        if (body.expectedRuntime && body.expectedRuntime !== runtime.runtime) return json(errorBody('invalid_state', 'Current Runtime differs from expectedRuntime'), 409)
        if (suffix.endsWith('/select')) { runtime.runtime = body.runtime; if (body.model) runtime.model = body.model }
        else if (suffix.endsWith('/model')) runtime.model = body.model
        else runtime.effort = body.effort
        runtime.revision += 1; audit('runtime.transition', 'succeeded', runtime.revision); return json(runtime)
      }
      if (suffix === '/configuration' && request.method === 'GET') return json(state.configuration)
      if (suffix === '/configuration' && request.method === 'PATCH') {
        if (request.headers['if-match'] !== state.configuration.revision) return json(errorBody('revision_conflict', 'Configuration changed; reload before saving.', { currentRevision: state.configuration.revision }), 412)
        state.configuration.desired = clone(body); state.configuration.restartRequired = true; state.configuration.revision = `sha256:${randomUUID().slice(0, 12)}`
        const op = operation('configuration.apply', instance); op.revision = state.configuration.revision; state.operations.unshift(op); audit('configuration.apply', 'succeeded', state.configuration.revision); return json(op, 202)
      }
      if (suffix === '/configuration/validate') return json({ valid: true, checks: [{ name: 'schema', state: 'succeeded', message: 'Allowlisted fields valid' }, { name: 'runtime', state: 'succeeded', message: 'Runtime installed' }, { name: 'openviking', state: 'succeeded', message: 'OV ready' }, { name: 'connectors', state: 'succeeded', message: 'Configuration accepted' }], restartRequired: JSON.stringify(body.desired) !== JSON.stringify(state.configuration.desired) })
      const processMatch = /^\/actions\/(start|stop|restart)$/.exec(suffix)
      if (processMatch) { const op = operation(`instance.${processMatch[1]}`, instance); state.operations.unshift(op); state.instances.items.find((x) => x.id === instance).ready = processMatch[1] !== 'stop'; audit(`instance.${processMatch[1]}`, 'succeeded', op.id); return json(op, 202) }
      const bindingMatch = /^\/model-bindings\/([^/]+)$/.exec(suffix)
      if (bindingMatch && request.method === 'PUT') { const provider = state.providers.find((x) => x.id === body.providerId); if (!provider) return json(errorBody('not_found', 'Provider not found'), 404); provider.bindings = provider.bindings.filter((x) => !(x.instanceId === instance && x.runtime === bindingMatch[1])); provider.bindings.push({ instanceId: instance, runtime: bindingMatch[1], state: bindingMatch[1] === 'dsh' ? 'restart_required' : 'synced' }); audit('provider.binding.update'); return json({ instanceId: instance, runtime: bindingMatch[1], providerId: body.providerId, model: body.model, state: bindingMatch[1] === 'dsh' ? 'restart_required' : 'synced' }) }
      if (suffix === '/logs') { const q = checkQuery(url, ['stream', 'before', 'after', 'limit']); if (q) return json(errorBody('invalid_argument', q), 400); const stream = url.searchParams.get('stream'); if (!['daemon', 'web'].includes(stream)) return json(errorBody('invalid_argument', 'Only daemon or web streams are supported'), 400); return json({ items: state.logs.filter((x) => x.stream === stream), nextCursor: null, generatedAt: now() }) }
      if (suffix === '/diagnostics' && request.method === 'GET') return json({ instanceId: instance, release: '0.5.2-preview', platform: 'linux/x64', drift: state.configuration.restartRequired, database: { state: 'ready' }, openviking: { state: 'ready', version: '0.5.4' }, connectors: { ready: 1, total: 2 }, runtime: { state: 'ready', name: state.overview.runtime.runtime }, generatedAt: now() })
      if (suffix === '/diagnostics/bundle' && request.method === 'POST') { const op = operation('diagnostics.bundle', instance, ['collect', 'redact', 'archive', 'verify']); state.operations.unshift(op); audit('diagnostics.bundle'); return json(op, 202) }
      return json(errorBody('not_found', `No mock route for ${request.method} ${pathname}`), 404)
    } catch (error) {
      json(errorBody(error.code || 'internal_error', error.message || 'Mock request failed'), error.status || 500)
    }
  })
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(port, host, resolve) })
  if (!quiet) console.log(`OVSA mock demo: http://${host}:${server.address().port}/?demo=1 (isolated data only)`)
  server.mockState = state
  return server
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  await startMockServer({ port: Number(process.env.PORT || 3740), quiet: false })
}
