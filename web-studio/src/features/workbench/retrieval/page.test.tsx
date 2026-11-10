// @vitest-environment jsdom
import { QueryClientProvider } from '@tanstack/react-query'
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  RouterProvider,
} from '@tanstack/react-router'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { queryClient } from '#/app/providers/query-client'
import i18n from '#/i18n'
import { Route } from '#/routes/retrieval/route'
import type * as ovApi from '#/shared/api/ov-client/index'
import * as api from '#/shared/api/retrieval'

vi.mock('#/shared/api/retrieval', async (importOriginal) => ({
  ...(await importOriginal<typeof api>()),
  fetchFind: vi.fn(),
  fetchSearch: vi.fn(),
  fetchGrep: vi.fn(),
  fetchGlob: vi.fn(),
}))
vi.mock('#/shared/api/ov-client/index', async (importOriginal) => ({
  ...(await importOriginal<typeof ovApi>()),
  getFsLs: vi.fn().mockResolvedValue({
    data: { status: 'ok', result: [] },
    headers: {},
    status: 200,
  }),
}))

function result(abstract: string): api.GroupedFindResult {
  return {
    memories: [],
    skills: [],
    total: 1,
    resources: [
      {
        uri: 'viking://resources/result.md',
        abstract,
        context_type: 'resource',
        level: 2,
        score: 0.8,
        category: '',
        match_reason: '',
      },
    ],
  }
}

beforeEach(async () => {
  sessionStorage.clear()
  await i18n.changeLanguage('en')
  for (const fetch of [
    api.fetchFind,
    api.fetchSearch,
    api.fetchGrep,
    api.fetchGlob,
  ]) {
    vi.mocked(fetch).mockReset().mockResolvedValue(result('First result'))
  }
})
afterEach(() => {
  cleanup()
  queryClient.clear()
})

async function mount(search = '') {
  const root = createRootRoute()
  const route = createRoute({
    getParentRoute: () => root,
    path: '/retrieval',
    validateSearch: Route.options.validateSearch,
    component: Route.options.component,
  })
  const router = createRouter({
    routeTree: root.addChildren([route]),
    history: createMemoryHistory({ initialEntries: [`/retrieval${search}`] }),
  })
  await router.load()
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return {
    router,
    user: userEvent.setup(),
    input: await screen.findByPlaceholderText(
      i18n.t(
        `retrieval:placeholders.${new URLSearchParams(search).get('mode') ?? 'find'}`,
      ),
    ),
  }
}

const modes = [
  ['find', api.fetchFind],
  ['search', api.fetchSearch],
  ['grep', api.fetchGrep],
  ['glob', api.fetchGlob],
] as const

it.each(modes)(
  'resubmits %s after success and shares pending requests',
  async (mode, fetch) => {
    const request = vi.mocked(fetch)
    const { input, user, router } = await mount(`?mode=${mode}`)
    await user.type(input, '  alpha  ')
    const submit = screen.getByRole('button', { name: 'Search' })
    await user.click(submit)
    await screen.findByText('First result')
    expect(request).toHaveBeenCalledTimes(1)
    const firstArgs = request.mock.calls[0]
    const url = router.state.location.href
    let resolve!: (value: api.GroupedFindResult) => void
    request.mockReturnValueOnce(
      new Promise((done) => {
        resolve = done
      }),
    )
    await user.dblClick(submit)
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
    await user.click(input)
    await user.keyboard('{Enter}')
    expect(request).toHaveBeenCalledTimes(2)
    await act(async () => resolve(result('Updated result')))
    await screen.findByText('Updated result')
    request.mockResolvedValueOnce(result('Enter result'))
    await user.keyboard('{Enter}')
    await screen.findByText('Enter result')
    expect(request).toHaveBeenCalledTimes(3)
    expect(request.mock.calls).toEqual([firstArgs, firstArgs, firstArgs])
    expect(router.state.location.href).toBe(url)
    expect(router.history.length).toBe(1)
  },
)

it.each(modes)(
  'retries %s after failure without editing the query',
  async (mode, fetch) => {
    const request = vi.mocked(fetch)
    request.mockRejectedValueOnce(new Error('Service unavailable'))
    const { input, user } = await mount(`?mode=${mode}`)
    await user.type(input, 'alpha{Enter}')
    await screen.findByText('Service unavailable')
    expect(request).toHaveBeenCalledTimes(1)
    await user.keyboard('{Enter}')
    await screen.findByText('First result')
    expect(request).toHaveBeenCalledTimes(2)
    request.mockRejectedValueOnce(new Error('Service unavailable again'))
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await screen.findByText('Service unavailable again')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await screen.findByText('First result')
    expect(request).toHaveBeenCalledTimes(4)
  },
)

it('submits changed queries and options without refetching the previous query', async () => {
  const { input, user, router } = await mount('?q=alpha')
  const request = vi.mocked(api.fetchFind)
  await screen.findByText('First result')
  const submit = screen.getByRole('button', { name: 'Search' })
  fireEvent.change(input, { target: { value: 'beta' } })
  expect(request).toHaveBeenCalledTimes(1)
  await user.click(submit)
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
  await waitFor(() => expect(queryClient.isFetching()).toBe(0))
  fireEvent.change(input, { target: { value: 'alpha' } })
  request.mockResolvedValueOnce(result('Revisited result'))
  await user.click(submit)
  await screen.findByText('Revisited result')
  await user.click(screen.getByRole('button', { name: 'More filters' }))
  fireEvent.change(
    screen.getByRole('spinbutton', { name: 'Minimum relevance' }),
    { target: { value: '0.4' } },
  )
  expect(request).toHaveBeenCalledTimes(3)
  await user.click(submit)
  await waitFor(() => expect(request).toHaveBeenCalledTimes(4))
  expect(
    request.mock.calls.map(([q, options]) => [q, options?.scoreThreshold]),
  ).toEqual([
    ['alpha', undefined],
    ['beta', undefined],
    ['alpha', undefined],
    ['alpha', 0.4],
  ])
  await waitFor(() =>
    expect(router.state.location.search).toEqual({ q: 'alpha', minScore: 0.4 }),
  )
})
