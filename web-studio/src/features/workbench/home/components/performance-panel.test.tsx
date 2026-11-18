// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { PerformancePanel } from './performance-panel'

const state = vi.hoisted(() => ({ fetch: vi.fn() }))
vi.mock('#/features/workbench/home/lib/performance-api', () => ({
  fetchPerformance: state.fetch,
}))
const summary = {
  observations: 1,
  observed_model_events: 1,
  unverified_usage_events: 1,
  unknown_usage_events: 0,
  attempt_observations: 0,
  adapter_observations: 0,
  failed_observations: 0,
  cancelled_observations: 0,
  token_totals: Object.fromEntries(
    ['input_tokens', 'output_tokens', 'cached_tokens', 'reasoning_tokens'].map(
      (key) => [key, { known_tokens: 0, unknown_events: 1 }],
    ),
  ),
  reported_token_totals: Object.fromEntries(
    ['input_tokens', 'output_tokens', 'cached_tokens', 'reasoning_tokens'].map(
      (key) => [key, 0],
    ),
  ),
  estimated_token_totals: Object.fromEntries(
    ['input_tokens', 'output_tokens', 'cached_tokens', 'reasoning_tokens'].map(
      (key) => [key, 0],
    ),
  ),
  unverified_token_totals: Object.fromEntries(
    ['input_tokens', 'output_tokens', 'cached_tokens', 'reasoning_tokens'].map(
      (key) => [key, 0],
    ),
  ),
  incomplete_usage_events: 0,
  duration_by_kind: {},
  cache_hit_rate: null,
  cache_covered_events: 0,
  observed_interval_union_seconds: 0,
  timed_interval_observations: 0,
}
const workflow = {
  ...summary,
  user_id: 'alice',
  workflow_id: 'synthetic-workflow',
  position: '2026-11-18T00:00:00+00:00',
}
const t = (key: string, values?: Record<string, unknown>) =>
  key + (values ? JSON.stringify(values) : '')
function view(enabled = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <PerformancePanel
        enabled={enabled}
        scope={{ account: 'a', user: 'alice' }}
        t={t}
      />
    </QueryClientProvider>,
  )
}
beforeEach(() => {
  state.fetch.mockReset().mockResolvedValue({
    enabled: true,
    coverage: 'observed',
    summary,
    items: [workflow],
    observed_before: '2026-11-18T01:00:00+00:00',
  })
})
afterEach(cleanup)

it('keeps unknown counts and cache coverage visible instead of implying measured zero', async () => {
  view()
  await screen.findByText('synthetic-workflow')
  expect(
    screen.getAllByText('performance.tokenValue{"value":0,"unknown":1}'),
  ).toHaveLength(4)
  expect(screen.getByText(/performance.cacheCoverage/).textContent).toContain(
    'performance.unknown',
  )
  expect(screen.getByRole('note').textContent).toContain('performance.coverage')
})
it('uses the list ingestion boundary and principal for details', async () => {
  state.fetch.mockImplementation(async (query) =>
    query.view === 'details'
      ? { enabled: true, summary, items: [] }
      : {
          enabled: true,
          summary,
          items: [workflow],
          observed_before: 'frozen-boundary',
        },
  )
  view()
  fireEvent.click(await screen.findByText('synthetic-workflow'))
  await waitFor(() =>
    expect(state.fetch).toHaveBeenCalledWith(
      expect.objectContaining({
        view: 'details',
        user_id: 'alice',
        workflow_id: 'synthetic-workflow',
        observed_before: 'frozen-boundary',
      }),
      expect.any(AbortSignal),
    ),
  )
})
it('does not request statistics before identity is known', () => {
  view(false)
  expect(state.fetch).not.toHaveBeenCalled()
  expect(screen.getByText('usageAccessRequired')).toBeTruthy()
})
it('handles disabled/old servers and exposes truncation', async () => {
  state.fetch.mockResolvedValueOnce({ enabled: false, unsupported: true })
  const first = view()
  await screen.findByText('performance.disabled')
  first.unmount()
  state.fetch.mockResolvedValue({
    enabled: true,
    coverage: 'truncated',
    summary,
    items: [],
  })
  view()
  await screen.findByRole('alert')
  expect(screen.getByRole('alert').textContent).toBe('performance.truncated')
})
it('applies filters with a new snapshot rather than reusing an old cursor', async () => {
  view()
  await screen.findByText('synthetic-workflow')
  fireEvent.change(screen.getByLabelText('performance.model'), {
    target: { value: 'synthetic-model' },
  })
  fireEvent.click(screen.getByText('performance.apply'))
  await waitFor(() =>
    expect(state.fetch).toHaveBeenLastCalledWith(
      expect.objectContaining({ model: 'synthetic-model', cursor: undefined }),
      expect.any(AbortSignal),
    ),
  )
})

it('keeps two user-scoped counters from the same producer distinct', async () => {
  state.fetch.mockResolvedValue({
    enabled: true,
    summary,
    items: [],
    collection: {
      producers: ['alice', 'bob'].map((user_id) => ({
        producer_id: 'shared-producer',
        user_id,
        accepted: 1,
        persisted: 1,
        dropped: 0,
        failed: 0,
        pending: 0,
        uncertain: 0,
        clean: 1,
      })),
    },
  })
  view()
  expect(
    (await screen.findByText(/performance.collector.*alice/)).textContent,
  ).toContain('alice')
  expect(screen.getByText(/performance.collector.*bob/).textContent).toContain(
    'bob',
  )
})
