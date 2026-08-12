import { describe, expect, it, vi } from 'vitest'

import {
  registerServiceWorker,
  resolveServiceWorkerPaths,
} from './service-worker'

describe('resolveServiceWorkerPaths', () => {
  it.each([
    ['/', { scope: '/', scriptUrl: '/service-worker.js' }],
    ['/studio', { scope: '/studio/', scriptUrl: '/studio/service-worker.js' }],
    ['/studio/', { scope: '/studio/', scriptUrl: '/studio/service-worker.js' }],
  ])('resolves the service worker scope for %s', (basePath, expected) => {
    expect(resolveServiceWorkerPaths(basePath)).toEqual(expected)
  })
})

describe('registerServiceWorker', () => {
  it('registers the script with the slash-terminated application scope', async () => {
    const registration = {} as ServiceWorkerRegistration
    const register = vi.fn().mockResolvedValue(registration)
    const serviceWorker = { register } as unknown as ServiceWorkerContainer

    await expect(registerServiceWorker(serviceWorker, '/studio')).resolves.toBe(
      registration,
    )
    expect(register).toHaveBeenCalledWith('/studio/service-worker.js', {
      scope: '/studio/',
    })
  })

  it('handles registration failures without rejecting', async () => {
    const error = new DOMException('Invalid scope', 'SecurityError')
    const register = vi.fn().mockRejectedValue(error)
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const serviceWorker = { register } as unknown as ServiceWorkerContainer

    await expect(
      registerServiceWorker(serviceWorker, '/studio'),
    ).resolves.toBeUndefined()
    expect(warn).toHaveBeenCalledWith(
      'OpenViking Studio service worker registration failed',
      error,
    )

    warn.mockRestore()
  })
})
