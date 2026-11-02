// @vitest-environment jsdom
import {
  isConnectionSettingsPath,
  newConnectionPath,
} from '#/features/connection/lib/studio-mode'
import { Route } from '#/routes/config'
import { isRedirect } from '@tanstack/react-router'
import { expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ needsSetup: true }))
vi.mock('#/features/connection/lib/onboarding', () => ({
  needsStudioSetup: () => state.needsSetup,
}))
it.each([
  [true, '/onboarding'],
  [false, '/settings'],
] as const)(
  'keeps /config as a compatibility redirect for setup=%s',
  (needsSetup, target) => {
    state.needsSetup = needsSetup
    let destination: unknown
    try {
      ;(Route.options.beforeLoad as () => never)()
    } catch (result) {
      destination = result
    }
    expect(isRedirect(destination)).toBe(true)
    if (isRedirect(destination)) {
      expect(destination.options.to).toBe(target)
      expect(destination.options.replace).toBe(true)
    }
  },
)
it('keeps local connection forms reachable while the server is unavailable', () => {
  for (const path of [
    '/connections/new',
    '/connections/saved-id',
    '/admin/connections/new',
    '/admin/connections/saved-id',
  ])
    expect(isConnectionSettingsPath(path)).toBe(true)
  expect(isConnectionSettingsPath('/onboarding')).toBe(false)
  expect(newConnectionPath('/admin/settings')).toBe('/admin/connections/new')
  expect(newConnectionPath('/settings')).toBe('/connections/new')
})
