// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { getStudioDeployment, resolveStudioDeployment } from './config'
import {
  isStudioConnectionAllowed,
  resolveStudioExperience,
} from './experience'

const production = 'https://api.vikingdb.cn-beijing.volces.com/openviking'
const staging = 'https://api.vikingdb-stg.cn-beijing.volces.com/openviking'
afterEach(() => {
  vi.unstubAllEnvs()
  delete window.__OV_STUDIO_CONFIG__
})

it.each(['/workbench-stg', '/workbench-stg/'])(
  'selects the STG hosted endpoint for router base %s without changing the edition',
  (basePath) => {
    expect(resolveStudioDeployment({ basePath })).toEqual({
      edition: 'self-hosted',
      environment: 'stg',
      hostedApiBaseUrl: staging,
    })
  },
)
it.each([
  '/',
  './',
  '/workbench/',
  '/workbench-stg-other/',
  '/other/workbench-stg/',
])('keeps production for non-STG router base %s', (basePath) => {
  expect(resolveStudioDeployment({ basePath }).hostedApiBaseUrl).toBe(
    production,
  )
})
it('honors explicit environment and endpoint overrides with runtime before build configuration', () => {
  vi.stubEnv('BASE_URL', '/')
  vi.stubEnv('VITE_STUDIO_ENVIRONMENT', 'stg')
  expect(getStudioDeployment().hostedApiBaseUrl).toBe(staging)
  vi.stubEnv('VITE_STUDIO_HOSTED_API_URL', 'https://build-proxy.example/ov')
  expect(getStudioDeployment().hostedApiBaseUrl).toBe(
    'https://build-proxy.example/ov',
  )
  window.__OV_STUDIO_CONFIG__ = {
    environment: 'production',
    hostedApiBaseUrl: 'https://runtime-proxy.example/ov',
  }
  expect(getStudioDeployment()).toMatchObject({
    environment: 'production',
    hostedApiBaseUrl: 'https://runtime-proxy.example/ov',
  })
})
it('reads the STG router base supplied by the deployment wrapper', () => {
  vi.stubEnv('BASE_URL', '/workbench-stg/')
  expect(getStudioDeployment().hostedApiBaseUrl).toBe(staging)
})
it('rejects a misspelled environment at startup', () => {
  expect(() => resolveStudioDeployment({ environment: 'stagging' })).toThrow()
})
it.each(['self-hosted', 'volcengine'] as const)(
  '%s uses STG consistently for login, editing and saved connection admission',
  (edition) => {
    const experience = resolveStudioExperience(
      resolveStudioDeployment({ edition, basePath: '/workbench-stg/' }),
    )
    const service = experience.auth.apiKey.services.find(
      (entry) => entry.provider === 'volcengine',
    )!
    expect(service.endpoint).toEqual({ kind: 'fixed', url: staging })
    expect(
      isStudioConnectionAllowed(
        { baseUrl: staging, serviceSelection: 'volcengine' },
        experience,
      ),
    ).toBe(true)
    expect(isStudioConnectionAllowed({ baseUrl: staging }, experience)).toBe(
      true,
    )
    expect(
      isStudioConnectionAllowed(
        { baseUrl: production, serviceSelection: 'volcengine' },
        experience,
      ),
    ).toBe(false)
    expect(isStudioConnectionAllowed({ baseUrl: production }, experience)).toBe(
      false,
    )
    expect(
      isStudioConnectionAllowed(
        { baseUrl: 'https://private.example', serviceSelection: 'opensource' },
        experience,
      ),
    ).toBe(edition === 'self-hosted')
  },
)
