// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import type * as Experience from '#/platform/deployment/experience'
import { resolveStudioDeployment } from '#/platform/deployment/config'
import {
  resolveStudioStartup,
  isStudioConnectionAllowed,
} from '#/platform/deployment/experience'
import { readStudioPreview, switchStudioPreview } from './studio-preview'

const context = vi.hoisted(() => ({ enabled: false }))
vi.mock('#/platform/deployment/experience', async (original) => ({
  ...(await original<typeof Experience>()),
  getStudioExperience: () => ({ preview: { enabled: context.enabled } }),
}))
afterEach(() => {
  sessionStorage.clear()
  context.enabled = false
  vi.unstubAllEnvs()
})

it('STG starts official and a private preview preserves the STG endpoint and admission checks', () => {
  const config = resolveStudioDeployment({ basePath: '/workbench-stg/' })
  const official = resolveStudioStartup(config)
  const own = {
    baseUrl: 'https://self.example',
    serviceSelection: 'opensource',
  }
  expect(official.auth.defaultPage).toBe('welcome')
  expect(isStudioConnectionAllowed(own, official)).toBe(false)
  const privatePreview = resolveStudioStartup(config, 'self-hosted')
  expect(privatePreview.auth.defaultPage).toBe('api-key')
  expect(privatePreview.auth.apiKey.services[0].endpoint).toEqual({
    kind: 'fixed',
    url: 'https://api.vikingdb-stg.cn-beijing.volces.com/openviking',
  })
  expect(isStudioConnectionAllowed(own, privatePreview)).toBe(true)
  expect(
    isStudioConnectionAllowed(
      {
        baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
        serviceSelection: 'volcengine',
      },
      privatePreview,
    ),
  ).toBe(false)
})

it.each(['self-hosted', 'volcengine', 'invalid'])(
  'ignores the %s preview override in production',
  (selection) => {
    const privateBuild = resolveStudioStartup(
      resolveStudioDeployment(),
      selection,
    )
    expect(privateBuild.auth.defaultPage).toBe('api-key')
    expect(privateBuild.preview.enabled).toBe(false)
    const officialBuild = resolveStudioStartup(
      resolveStudioDeployment({ edition: 'volcengine' }),
      selection,
    )
    expect(officialBuild.auth.defaultPage).toBe('welcome')
    expect(officialBuild.preview.enabled).toBe(false)
  },
)

it('persists only the selected edition per tab and reloads at the Vite base login', () => {
  context.enabled = true
  const replace = vi.fn()
  switchStudioPreview('self-hosted', { storage: sessionStorage, replace })
  expect(readStudioPreview()).toBe('self-hosted')
  expect(replace).toHaveBeenCalledExactlyOnceWith('/login')
  expect(sessionStorage.length).toBe(1)
})

it('does nothing in production and does not navigate on storage failure', () => {
  const replace = vi.fn()
  const storage = {
    setItem: vi.fn(() => {
      throw new Error('blocked')
    }),
  } as unknown as Storage
  switchStudioPreview('self-hosted', { storage, replace })
  expect(storage.setItem).not.toHaveBeenCalled()
  context.enabled = true
  expect(() =>
    switchStudioPreview('self-hosted', { storage, replace }),
  ).toThrow('blocked')
  expect(replace).not.toHaveBeenCalled()
})

it('keeps the /workbench-stg prefix on a preview reload', async () => {
  vi.stubEnv('BASE_URL', '/workbench-stg/')
  vi.resetModules()
  const { switchStudioPreview: switchPreview } =
    await import('./studio-preview')
  context.enabled = true
  const replace = vi.fn()
  switchPreview('self-hosted', { storage: sessionStorage, replace })
  expect(replace).toHaveBeenCalledExactlyOnceWith('/workbench-stg/login')
})
