// @vitest-environment jsdom
import { Route } from '#/routes/admin/index'
import { cleanup, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async (original) => ({
  ...(await original<object>()),
  Navigate: ({ to }: { to: string }) => <output>{to}</output>,
}))
const state = vi.hoisted(() => ({ mode: 'api_key' }))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ serverMode: state.mode }),
}))
afterEach(() => {
  cleanup()
  state.mode = 'api_key'
})
it('opens the user management page directly instead of an empty landing page', async () => {
  const Index = Route.options.component as ComponentType & {
    preload?: () => Promise<unknown>
  }
  await Index.preload?.()
  render(<Index />)
  expect(screen.getByText('/admin/users')).toBeTruthy()
})

it('opens server configuration in local development without unavailable membership APIs', async () => {
  state.mode = 'dev'
  const Index = Route.options.component as ComponentType
  render(<Index />)
  expect(screen.getByText('/admin/configuration/server')).toBeTruthy()
})
