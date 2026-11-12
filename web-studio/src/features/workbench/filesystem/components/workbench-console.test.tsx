// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { WorkbenchConsole } from './workbench-console'

const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  invalidateAll: vi.fn(),
}))

vi.mock('@tanstack/react-query', () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}))

vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test-identity' }),
}))
vi.mock('#/features/workbench/resources/hooks/viking-fm', () => ({
  useInvalidateVikingFs: () => ({ invalidateAll: mocks.invalidateAll }),
}))
vi.mock('#/shared/api/ov-client/index', () => ({
  getOvClient: () => ({ instance: { request: mocks.request } }),
  isOvClientError: () => false,
}))

afterEach(cleanup)

it('uses the Console shortcuts and routes its commands through Studio’s data client', async () => {
  mocks.request.mockResolvedValue({
    status: 200,
    data: { status: 'ok', result: [] },
  })
  render(
    <WorkbenchConsole mode="command" onOpenResource={vi.fn()} toolbar={null} />,
  )
  const input = screen.getByRole('textbox')
  fireEvent.click(screen.getByRole('button', { name: /^compile/i }))
  expect(input).toHaveProperty('value', 'ov compile ')
  expect(mocks.request).not.toHaveBeenCalled()

  fireEvent.change(input, { target: { value: 'ov ls' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  await waitFor(() => expect(mocks.request).toHaveBeenCalledTimes(1))

  const request = mocks.request.mock.lastCall?.[0]
  const url = new URL(request.url, window.location.origin)
  expect(url.pathname).toBe('/api/v1/fs/ls')
  expect(url.searchParams.get('uri')).toBe('viking://')
  expect(request.method).toBe('GET')
  expect(request.signal).toBeInstanceOf(AbortSignal)
  expect(mocks.invalidateAll).not.toHaveBeenCalled()
})
