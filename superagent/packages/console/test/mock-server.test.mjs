import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { startMockServer } from './mock-server.mjs'

let server
let origin
before(async () => { server = await startMockServer(); origin = `http://127.0.0.1:${server.address().port}` })
after(() => new Promise((resolve) => { server.close(resolve); server.closeAllConnections?.() }))
const call = (path, options = {}) => fetch(`${origin}${path}`, options)
const mutation = (path, body, headers = {}, method = 'POST') => call(path, { method, headers: { 'content-type': 'application/json', 'x-csrf-token': 'demo-owner', ...headers }, body: JSON.stringify(body) })

test('mock discovery exposes explicit three-state modules', async () => {
  const response = await call('/api/v1/capabilities?instance=primary')
  assert.equal(response.status, 200)
  const body = await response.json()
  assert.deepEqual(Object.values(body.modules).every((module) => module.effective === (module.enabled && module.available)), true)
  assert.equal(body.modules['tasks.pause'].effective, false)
  assert.equal(body.modules['configuration.write'].effective, true)
})

test('mutations require CSRF and viewer cannot read protected content', async () => {
  assert.equal((await call('/api/v1/instances/primary/tasks/t203/actions/cancel', { method: 'POST', body: '{}' })).status, 403)
  assert.equal((await call('/api/v1/instances/primary/configuration', { headers: { 'x-demo-role': 'viewer' } })).status, 403)
  assert.equal((await call('/api/v1/instances/primary/overview', { headers: { 'x-demo-role': 'viewer' } })).status, 200)
})

test('task cancellation checks revision, preserves pending termination, and repeats idempotently', async () => {
  const conflict = await mutation('/api/v1/instances/primary/tasks/t203/actions/cancel', { expectedRevision: 1, reason: 'test' })
  assert.equal(conflict.status, 412)
  assert.equal((await conflict.json()).error.code, 'revision_conflict')
  const options = { headers: { 'idempotency-key': 'cancel-demo-1' } }
  const first = await mutation('/api/v1/instances/primary/tasks/t203/actions/cancel', { expectedRevision: 4, reason: 'test' }, options.headers)
  assert.equal(first.status, 200)
  const body = await first.json()
  assert.equal(body.worker.stopRequested, true)
  assert.equal(body.worker.termination, 'pending')
  const replay = await mutation('/api/v1/instances/primary/tasks/t203/actions/cancel', { expectedRevision: 4, reason: 'test' }, options.headers)
  assert.deepEqual(await replay.json(), body)
})

test('configuration uses If-Match and returns an Operation rather than claiming effective', async () => {
  const config = await (await call('/api/v1/instances/primary/configuration')).json()
  const stale = await mutation('/api/v1/instances/primary/configuration', config.desired, { 'if-match': 'sha256:stale' }, 'PATCH')
  assert.equal(stale.status, 412)
  const response = await mutation('/api/v1/instances/primary/configuration', { ...config.desired, effort: 'high' }, { 'if-match': config.revision }, 'PATCH')
  assert.equal(response.status, 202)
  const operation = await response.json()
  assert.equal(operation.kind, 'configuration.apply')
  const updated = await (await call('/api/v1/instances/primary/configuration')).json()
  assert.equal(updated.restartRequired, true)
  assert.equal(updated.effective.model, config.effective.model)
})

test('credential writes return only a reference and audit never contains the secret', async () => {
  const secret = 'demo-secret-must-not-leak'
  const response = await mutation('/api/v1/model-providers/ark-main/credential', { secret }, {}, 'PUT')
  const body = await response.json()
  assert.equal(response.status, 200)
  assert.ok(body.credentialRef)
  assert.equal(JSON.stringify(body).includes(secret), false)
  const audit = await (await call('/api/v1/audit')).text()
  assert.equal(audit.includes(secret), false)
  const providers = await (await call('/api/v1/model-providers')).text()
  assert.equal(providers.includes(secret), false)
})

test('query validation and log stream allowlist return RFC errors', async () => {
  const unknown = await call('/api/v1/instances/primary/tasks?bogus=1')
  assert.equal(unknown.status, 400)
  assert.equal((await unknown.json()).error.code, 'invalid_argument')
  assert.equal((await call('/api/v1/instances/primary/tasks?limit=1&limit=2')).status, 400)
  assert.equal((await call('/api/v1/instances/primary/logs?stream=/etc/passwd')).status, 400)
})
