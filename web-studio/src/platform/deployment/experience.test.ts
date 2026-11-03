// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { resolveStudioDeployment } from './config'
import {
  resolveStudioExperience,
  isStudioConnectionAllowed,
  supportsNativeManagement,
} from './experience'
import {
  AuthMethod,
  LoginEntry,
  ServiceProvider,
  StudioEdition,
} from '../studio-contract'
import { resolveStudioEntry } from '../entry/resolve-entry'

afterEach(() => {
  delete window.__OV_STUDIO_CONFIG__
  vi.unstubAllEnvs()
})

it('keeps hosted API Key login identical in official and self-hosted deployments', () => {
  const hostedApiBaseUrl = 'https://cloud.example/ov'
  const self = resolveStudioExperience(
    resolveStudioDeployment({
      edition: StudioEdition.SelfHosted,
      hostedApiBaseUrl,
    }),
  )
  const cloud = resolveStudioExperience(
    resolveStudioDeployment({
      edition: StudioEdition.Volcengine,
      hostedApiBaseUrl,
    }),
  )
  expect(self.auth.defaultPage).toBe(AuthMethod.ApiKey)
  expect(cloud.auth.defaultPage).toBe('welcome')
  expect(self.auth.apiKey.services.map((service) => service.entry)).toEqual([
    LoginEntry.VolcengineApiKey,
    LoginEntry.OpenVikingApiKey,
  ])
  expect(cloud.auth.apiKey.services.map((service) => service.entry)).toEqual([
    LoginEntry.VolcengineApiKey,
  ])
  expect(self.auth.apiKey.services[0].endpoint).toEqual(
    cloud.auth.apiKey.services[0].endpoint,
  )
  expect(self.auth.apiKey.services[0].termsUrl).toContain('openviking-personal')
  expect(cloud.auth.apiKey.services[0].termsUrl).toContain('/viking-product')
  expect(cloud.auth.apiKey.services[0].endpoint).toEqual({
    kind: 'fixed',
    url: hostedApiBaseUrl,
  })
  const ownService = {
    baseUrl: 'https://my-domain.example',
    serviceSelection: ServiceProvider.OpenSource,
  }
  const hostedService = {
    baseUrl: hostedApiBaseUrl,
    serviceSelection: ServiceProvider.Volcengine,
  }
  expect(isStudioConnectionAllowed(ownService, self)).toBe(true)
  expect(isStudioConnectionAllowed(hostedService, self)).toBe(true)
  expect(isStudioConnectionAllowed(ownService, cloud)).toBe(false)
  expect(isStudioConnectionAllowed(hostedService, cloud)).toBe(true)
  expect(supportsNativeManagement(ServiceProvider.Volcengine, self)).toBe(false)
})

it.each([
  ['root', '', true, '/admin'],
  ['admin', 'synthetic-data', true, '/home'],
  ['user', 'synthetic-data', false, '/home'],
] as const)(
  'keeps %s entry independent of login form selection',
  (role, _apiKey, canManage, target) => {
    const self = resolveStudioExperience(resolveStudioDeployment())
    expect(
      resolveStudioEntry(
        {
          access:
            role === 'root' ? 'management' : canManage ? 'both' : 'workbench',
          provider: ServiceProvider.OpenSource,
          preferredMode: null,
        },
        self,
      ),
    ).toBe(target)
  },
)

it('selects the production profile once at startup, independent of later global edits', async () => {
  vi.resetModules()
  const { initializeStudioExperience, getStudioExperience } =
    await import('./experience')
  window.__OV_STUDIO_CONFIG__ = { edition: StudioEdition.Volcengine }
  const startup = initializeStudioExperience()
  window.__OV_STUDIO_CONFIG__ = { edition: StudioEdition.SelfHosted }
  expect(getStudioExperience()).toBe(startup)
  expect(initializeStudioExperience()).toBe(startup)
  expect(startup.auth.defaultPage).toBe('welcome')
})
