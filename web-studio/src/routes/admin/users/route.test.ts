import { routeTree } from '#/routeTree.gen'
import { createMemoryHistory, createRouter } from '@tanstack/react-router'
import { expect, it } from 'vitest'

it.each([
  ['/admin/users', '/admin/users/'],
  ['/admin/users/groups', '/admin/users/groups'],
  ['/admin/users/permissions', '/admin/users/permissions'],
  ['/admin/users/memory-templates', '/admin/users/memory-templates'],
])('matches the user and permissions tab at %s', (path, routeId) => {
  const router = createRouter({
    routeTree,
    history: createMemoryHistory({ initialEntries: [path] }),
  })
  expect(router.matchRoutes(path).at(-1)?.routeId).toBe(routeId)
})
