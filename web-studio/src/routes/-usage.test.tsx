// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { UsagePage } from '#/features/workbench/home/usage-page'
import { validateUsageSearch } from '#/features/workbench/home/lib/usage-navigation'

const state = vi.hoisted(() => ({ search: {} as Record<string, unknown> }))
vi.mock('#/features/workbench/home/page', () => ({
  HomePage: () => <h1>Usage</h1>,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: unknown) => options,
  useSearch: () => validateUsageSearch(state.search),
  Link: ({
    to,
    search,
    children,
  }: {
    to: string
    search: Record<string, string | undefined>
    children: ReactNode
  }) => {
    const query = new URLSearchParams()
    Object.entries(search).forEach(([key, value]) => {
      if (value) query.set(key, value)
    })
    return <a href={`${to}${query.size ? `?${query}` : ''}`}>{children}</a>
  },
}))
afterEach(cleanup)
it('returns to the selected workbench file and panel', () => {
  state.search = {
    file: 'viking://resources/example.md',
    uri: 'viking://resources/',
    panel: 'agent',
    session: 'session-1',
    upload: true,
  }
  render(<UsagePage />)
  const target = new URL(
    screen.getByRole('link', { name: 'backToWorkbench' }).getAttribute('href')!,
    'http://localhost',
  )
  expect(target.pathname).toBe('/filesystem')
  expect(target.searchParams.get('file')).toBe('viking://resources/example.md')
  expect(target.searchParams.get('panel')).toBe('agent')
  expect(target.searchParams.get('session')).toBe('session-1')
  expect(target.searchParams.has('upload')).toBe(false)
})
it('has a workbench fallback when the usage page is opened directly', () => {
  state.search = { panel: 'invalid', file: 123 }
  render(<UsagePage />)
  expect(
    screen.getByRole('link', { name: 'backToWorkbench' }).getAttribute('href'),
  ).toBe('/filesystem')
})
