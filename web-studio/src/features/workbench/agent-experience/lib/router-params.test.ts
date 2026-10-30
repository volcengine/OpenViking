import { describe, expect, it } from 'vitest'

// These tests pin down the @tanstack/router param round-trip that the list and
// detail views rely on: Link params are percent-encoded when the href is built
// and decoded again when matched, so callers must pass the RAW viking URI.
describe('router param round-trip', () => {
  it('encodes params into the href and decodes them on match', async () => {
    const { createMemoryHistory, createRouter, createRootRoute, createRoute } =
      await import('@tanstack/react-router')

    const rawUri = 'viking://user/default/memories/experiences/exchange flow.md'
    const rootRoute = createRootRoute()
    const detailRoute = createRoute({
      getParentRoute: () => rootRoute,
      path: '/agent-experience/$experienceUri',
    })
    const routeTree = rootRoute.addChildren([detailRoute])
    const router = createRouter({
      history: createMemoryHistory({ initialEntries: ['/'] }),
      routeTree,
    })
    await router.load()

    const location = await router.buildLocation({
      params: { experienceUri: rawUri },
      to: '/agent-experience/$experienceUri',
    })

    expect(location.href).not.toContain('://')
    expect(decodeURIComponent(location.href)).toContain(rawUri)

    // Navigate and inspect the matched params.
    await router.navigate({
      params: { experienceUri: rawUri },
      to: '/agent-experience/$experienceUri',
    })
    await router.load()
    const match = router.state.matches.find(
      (item) => item.routeId === '/agent-experience/$experienceUri',
    )
    expect(match?.params.experienceUri).toBe(rawUri)
  })
})
