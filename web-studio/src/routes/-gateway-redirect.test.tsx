import { Route } from '#/routes/gateway'
import { isRedirect } from '@tanstack/react-router'
import { expect, it } from 'vitest'

it.each([
  ['/gateway', '', '', '/admin/gateway'],
  ['/gateway/profiles/p1', '', '', '/admin/gateway/profiles/p1'],
  [
    '/gateway/requests',
    '?filter=issues',
    'recent',
    '/admin/gateway/requests?filter=issues#recent',
  ],
])(
  'redirects legacy %s while preserving its location',
  (pathname, searchStr, hash, target) => {
    const beforeLoad = Route.options.beforeLoad as (context: {
      location: { pathname: string; searchStr: string; hash: string }
    }) => never
    let redirect: unknown
    try {
      beforeLoad({ location: { pathname, searchStr, hash } })
    } catch (result) {
      redirect = result
    }
    expect(isRedirect(redirect)).toBe(true)
    if (isRedirect(redirect)) {
      expect(redirect.options.href).toBe(target)
      expect(redirect.options.replace).toBe(true)
    }
  },
)
