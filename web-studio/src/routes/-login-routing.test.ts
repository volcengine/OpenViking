// @vitest-environment jsdom
import { createMemoryHistory, createRouter } from '@tanstack/react-router'
import { expect, it } from 'vitest'
import { routeTree } from '#/routeTree.gen'
import { AUTH_PATHS } from '#/features/auth/lib/auth-routes'

it.each(['', '/studio'])(
  'registers explicit login and redirects old bookmarks under %s',
  async (basepath) => {
    for (const path of AUTH_PATHS.flatMap((entry) => [entry, `${entry}/`])) {
      const router = createRouter({
        routeTree,
        basepath,
        history: createMemoryHistory({
          initialEntries: [`${basepath}${path}`],
        }),
      })
      expect(
        router.matchRoutes(router.state.location.pathname).at(-1)?.routeId,
      ).toBe(
        path.replace(/\/+$/, '') === '/login'
          ? '/login/'
          : path.replace(/\/+$/, ''),
      )
    }
    const router = createRouter({
      routeTree,
      basepath,
      history: createMemoryHistory({
        initialEntries: [`${basepath}/onboarding`],
      }),
    })
    await router.load()
    await expect.poll(() => router.state.location.pathname).toBe('/login')
    expect(router.history.location.pathname).toBe(`${basepath}/login`)
    expect(router.history.length).toBe(1)
  },
)
