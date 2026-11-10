// @vitest-environment jsdom
import {
  skillScope,
  useCompileSkills,
} from '#/features/workbench/compile/lib/skills'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

const fetchSkills = vi.hoisted(() => vi.fn())
vi.mock('#/features/workbench/compile/lib/api', () => ({
  BUILTIN_SKILLS_ROOT: 'viking://system/compile/example-skills',
  fetchCompileSkills: fetchSkills,
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    identityScopeKey: 'alice',
    connection: { userId: 'alice' },
  }),
}))
const clients: QueryClient[] = []
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
  vi.resetAllMocks()
})
it('keeps built-ins usable without personal skills and when shared discovery fails', async () => {
  fetchSkills.mockImplementation(async (_signal: AbortSignal, root: string) => {
    if (root === 'viking://agent/skills') throw new Error('Shared unavailable')
    if (root === 'viking://user/alice/skills') return []
    return [
      {
        uri: `${root}/future-skill`,
        name: 'Future skill',
        description: 'From the server',
      },
    ]
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  const { result } = renderHook(useCompileSkills, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
  await waitFor(() => expect(result.current.shared.isError).toBe(true))
  expect(result.current.mine.data).toEqual([])
  expect(result.current.data.map((skill) => skill.name)).toEqual([
    'Future skill',
  ])
  expect(skillScope(result.current.data[0].uri)).toBe('builtinSkills')
})
