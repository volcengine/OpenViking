import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from '@tanstack/react-router'
import { expect, it } from 'vitest'
import { compileListReturn } from './navigation'

it('restores the original filtered history entry and scroll-restoration key', async () => {
  const root = createRootRoute()
  const list = createRoute({
    getParentRoute: () => root,
    path: '/compile',
    validateSearch: (search: Record<string, unknown>) => ({
      status: String(search.status || ''),
      q: String(search.q || ''),
    }),
  })
  const detail = createRoute({
    getParentRoute: () => root,
    path: '/compile/tasks/$taskId',
  })
  const router = createRouter({
    routeTree: root.addChildren([list, detail]),
    history: createMemoryHistory({
      initialEntries: ['/compile?status=running&q=wiki'],
    }),
    scrollRestoration: true,
  })
  await router.load()
  const original = router.state.location
  await router.navigate({
    to: '/compile/tasks/$taskId',
    params: { taskId: 'task-1' },
    state: {
      compileListOrigin: {
        scope: 'alice',
        index: original.state.__TSR_index,
        search: { status: 'running', q: 'wiki' },
      },
    },
  })
  const location = router.state.location
  const destination = compileListReturn(
    location.state.compileListOrigin,
    'alice',
    location.state.__TSR_index,
  )
  expect(destination.search).toEqual({ status: 'running', q: 'wiki' })
  expect(destination.restoreHistory).toBe(true)
  router.history.back()
  await router.load()
  expect(router.state.location.href).toBe(original.href)
  expect(router.state.location.state.__TSR_key).toBe(original.state.__TSR_key)
})

it('defaults to the list for direct links or a different identity', () => {
  expect(compileListReturn(undefined, 'alice', 0)).toEqual({
    search: {},
    restoreHistory: false,
  })
  expect(
    compileListReturn(
      { scope: 'alice', index: 0, search: { q: 'private-path' } },
      'bob',
      1,
    ),
  ).toEqual({ search: {}, restoreHistory: false })
})
