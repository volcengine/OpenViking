import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, legacyCapabilities, setStoredApiKey } from './api'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

afterEach(() => vi.unstubAllGlobals())

describe('RFC API boundary', () => {
  it('uses only same-origin GET requests and forwards cursor filters', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ items: [], nextCursor: null }))
    vi.stubGlobal('fetch', fetchMock)
    await api.tasks('primary', { status: 'running', before: '42', limit: 20 })
    const [url, options] = fetchMock.mock.calls[0]
    expect(String(url)).toBe('http://localhost:3000/api/v1/instances/primary/tasks?instance=primary&status=running&before=42&limit=20')
    expect(options.method).toBeUndefined()
    expect(options.cache).toBe('no-store')
  })

  it('falls back to honest legacy capabilities on an unimplemented v1 endpoint', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ error: 'not_found' }, 404)))
    const value = await api.capabilities('legacy')
    expect(value.source).toBe('legacy')
    expect(value.modules['activity.read'].effective).toBe(true)
    expect(value.modules['tasks.read']).toMatchObject({ available: false, effective: false, reasonCode: 'read_api_missing' })
    expect(value.modules['tasks.control'].effective).toBe(false)
    expect(value.modules['tasks.pause'].reasonCode).toBe('not_provided')
  })

  it('signs its requests with the stored key, and asks for one on 401', async () => {
    setStoredApiKey('k-123')
    const denied = vi.fn().mockResolvedValue(json({ error: 'unauthorized' }, 401))
    vi.stubGlobal('fetch', denied)
    await expect(api.instances()).rejects.toMatchObject({ status: 401, code: 'unauthorized' })
    expect(denied.mock.calls[0][1].headers.authorization).toBe('Bearer k-123')
    setStoredApiKey('')
    const anonymous = vi.fn().mockResolvedValue(json({ items: [], defaultInstance: 'agent' }))
    vi.stubGlobal('fetch', anonymous)
    await api.instances()
    expect(anonymous.mock.calls[0][1].headers.authorization).toBeUndefined()
  })

  it('keeps unavailable control modules distinct from enabled read modules', () => {
    const value = legacyCapabilities('legacy')
    expect(value.modules['runtime.read']).toMatchObject({ available: false, effective: false, reasonCode: 'runtime_unsupported' })
    expect(value.modules['runtime.control']).toMatchObject({ enabled: false, available: false, effective: false })
  })
})
