import axios from 'axios'
import { afterEach, expect, it, vi } from 'vitest'
import { createResourceAclApi, isSharedAclTarget } from './resource-acl'
import type { InternalAxiosRequestConfig } from 'axios'

afterEach(() => vi.restoreAllMocks())
const connection = {
  baseUrl: 'http://localhost:1933',
  accountId: 'acme',
  userId: 'alice',
  adminApiKey: 'control-key',
  apiKey: 'user-key',
}
function transport(result: unknown) {
  const requests: InternalAxiosRequestConfig[] = []
  const instance = axios.create({
    adapter: async (config) => {
      requests.push(config)
      return {
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
        data: { status: 'ok', result },
      }
    },
  })
  vi.spyOn(axios, 'create').mockReturnValue(instance)
  return requests
}
it('separates configuration credentials from resource data credentials and patches only ACL', async () => {
  const requests = transport({ settings: { acl: { enabled: true } } })
  const api = createResourceAclApi(connection, false)
  expect(await api.getEnabled()).toBe(true)
  await api.setEnabled(false)
  await api.get('viking://resources/project')
  expect(requests.map((request) => request.headers.get('X-API-Key'))).toEqual([
    'control-key',
    'control-key',
    'user-key',
  ])
  expect(JSON.parse(requests[1].data)).toEqual({
    settings: { acl: { enabled: false } },
  })
  expect(requests[0].url).toContain('/accounts/acme/configuration')
  expect(requests[2].headers.get('X-OpenViking-User')).toBeUndefined()
})
it('uses an authenticated account-admin control key for ACL without changing the browsing identity', async () => {
  const requests = transport({})
  const api = createResourceAclApi(connection, false, true)
  await api.get('viking://resources/project')
  await api.listDirectory('viking://resources/project/')
  await api.change('viking://resources/project', {
    kind: 'grant',
    principal: 'user:bob',
    level: 'read',
  })
  expect(requests.map((request) => request.headers.get('X-API-Key'))).toEqual([
    'control-key',
    'control-key',
    'control-key',
  ])
  expect(requests[0].headers.get('X-OpenViking-User')).toBeUndefined()
  expect(requests[1].url).toContain('/fs/ls')
})
it('preserves trusted identity and makes targeted grants without replacing other entries', async () => {
  const requests = transport({})
  const api = createResourceAclApi({ ...connection, apiKey: '' }, true)
  await api.change('viking://resources/project', {
    kind: 'grant',
    principal: 'group:engineering',
    level: 'write',
  })
  await api.change('viking://resources/project', {
    kind: 'revoke',
    principal: 'user:bob',
  })
  await api.change('viking://resources/project', {
    kind: 'mode',
    mode: 'restricted',
  })
  expect(requests[0].headers.get('X-OpenViking-User')).toBe('alice')
  expect(requests[0].headers.get('X-OpenViking-Account')).toBe('acme')
  expect(requests[0].url).toContain('/acl/grant')
  expect(JSON.parse(requests[0].data)).toEqual({
    uri: 'viking://resources/project',
    principal: 'group:engineering',
    level: 'write',
  })
  expect(requests[1].url).toContain('/acl/revoke')
  expect(JSON.parse(requests[2].data)).toEqual({
    uri: 'viking://resources/project',
    acl_mode: 'restricted',
  })
})
it('clears a directory ACL through the reset endpoint', async () => {
  const requests = transport({})
  const api = createResourceAclApi(connection, false, true)
  await api.change('viking://resources/project', { kind: 'reset' })
  expect(requests[0].method).toBe('delete')
  expect(requests[0].url).toContain('/api/v1/acl')
  expect(
    new URL(requests[0].url!, 'http://localhost').searchParams.get('uri'),
  ).toBe('viking://resources/project')
})
it('offers ACL only on concrete shared resources', () => {
  for (const uri of [
    'viking://resources/a',
    'viking://resources/a/file.md',
    'viking://resources/a/',
  ])
    expect(isSharedAclTarget(uri)).toBe(true)
  for (const uri of [
    'viking://resources',
    'viking://resources/',
    'viking://user/alice/resources/a',
    'viking://resources/../user/alice',
    'viking://resources//a',
    'viking://',
  ])
    expect(isSharedAclTarget(uri)).toBe(false)
})
