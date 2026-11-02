// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import {
  getStudioDeployment,
  isStudioConnectionAllowed,
  resolveStudioDeployment,
} from './studio-deployment'
import { canOpenManagement } from '#/features/connection/lib/studio-mode'
import { canEditStudioConfiguration } from '#/features/connection/lib/studio-permissions'

afterEach(() => {
  delete window.__OV_STUDIO_CONFIG__
  vi.unstubAllEnvs()
})
it('defaults to self-hosted and permits user-defined domains', () => {
  expect(getStudioDeployment().edition).toBe('self-hosted')
  expect(
    isStudioConnectionAllowed({
      baseUrl: 'https://my-company.example',
      serviceSelection: 'opensource',
    }),
  ).toBe(true)
})
it('resolves explicit build and runtime editions independently of the connected service', () => {
  vi.stubEnv('VITE_STUDIO_EDITION', 'volcengine')
  expect(getStudioDeployment().edition).toBe('volcengine')
  window.__OV_STUDIO_CONFIG__ = { edition: 'self-hosted' }
  expect(getStudioDeployment().edition).toBe('self-hosted')
})
it.each([
  { edition: 'typo' },
  { hostedApiBaseUrl: 'http://cloud.example' },
  { hostedApiBaseUrl: 'https://key@cloud.example' },
  { hostedApiBaseUrl: 'https://cloud.example?secret=key' },
])('rejects invalid deployment configuration %j', (config) => {
  expect(() => resolveStudioDeployment(config)).toThrow()
})
it('official deployment cannot revive a self-hosted connection or grant ROOT administration', () => {
  window.__OV_STUDIO_CONFIG__ = { edition: 'volcengine' }
  expect(
    isStudioConnectionAllowed({
      baseUrl: 'https://private.example',
      serviceSelection: 'opensource',
    }),
  ).toBe(false)
  expect(
    canEditStudioConfiguration({
      role: 'root',
      serverMode: 'dev',
      isRoleLoading: false,
      hasControlCredential: false,
    }),
  ).toBe(false)
  expect(
    isStudioConnectionAllowed({
      baseUrl: getStudioDeployment().hostedApiBaseUrl,
      serviceSelection: 'volcengine',
    }),
  ).toBe(true)
  expect(
    canOpenManagement({
      provider: 'opensource',
      connection: {
        baseUrl: 'https://private.example',
        accountId: 'default',
        userId: '',
        apiKey: '',
        adminApiKey: 'synthetic-root',
      },
      role: 'root',
      isRoleLoading: false,
      serverMode: 'api_key',
    }),
  ).toBe(false)
})
