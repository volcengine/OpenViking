// @vitest-environment jsdom

import { createMemoryHistory, createRouter } from '@tanstack/react-router'
import { expect, it } from 'vitest'

import { routeTree } from '#/routeTree.gen'

it.each(
  ['', '/studio'].flatMap((basepath) =>
    ['/playground', '/playground/'].map((path) => ({
      basepath,
      bookmark: `${basepath}${path}`,
    })),
  ),
)(
  'redirects old $bookmark bookmarks without losing their selection',
  async ({ basepath, bookmark }) => {
    const router = createRouter({
      routeTree,
      basepath,
      history: createMemoryHistory({
        initialEntries: [
          `${bookmark}?file=viking%3A%2F%2Fresources%2Fguide.md&uri=viking%3A%2F%2Fresources%2F&panel=agent&session=session-123&upload=true#L10`,
        ],
      }),
    })

    await router.load()

    await expect.poll(() => router.state.location.pathname).toBe('/filesystem')
    expect(router.history.location.pathname).toBe(`${basepath}/filesystem`)
    expect(router.state.location.search).toEqual({
      file: 'viking://resources/guide.md',
      uri: 'viking://resources/',
      panel: 'agent',
      session: 'session-123',
      upload: true,
    })
    expect(router.state.location.hash).toBe('L10')
    expect(router.history.length).toBe(1)
  },
)

it.each(['', '/studio'])('matches the canonical route under %s', (basepath) => {
  const path = `${basepath}/filesystem`
  const router = createRouter({
    routeTree,
    basepath,
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  expect(
    router.matchRoutes(router.state.location.pathname).at(-1)?.routeId,
  ).toBe('/filesystem')
})
