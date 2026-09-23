import { createMemoryHistory, createRouter } from '@tanstack/react-router'
import { expect, it } from 'vitest'
import { routeTree } from '#/routeTree.gen'

it.each([
  ['/users', '/users/'],
  ['/users/groups', '/users/groups'],
  ['/users/permissions', '/users/permissions'],
])('matches the user and permissions tab at %s', (path, routeId) => {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  expect(router.matchRoutes(path).at(-1)?.routeId).toBe(routeId)
})
