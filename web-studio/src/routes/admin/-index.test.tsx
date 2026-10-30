// @vitest-environment jsdom
import { Route } from '#/routes/admin/index'
import { cleanup, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async (original) => ({
  ...(await original<object>()),
  Navigate: ({ to }: { to: string }) => <output>{to}</output>,
}))
afterEach(cleanup)
it('opens the user management page directly instead of an empty landing page', async () => {
  const Index = Route.options.component as ComponentType & {
    preload?: () => Promise<unknown>
  }
  await Index.preload?.()
  render(<Index />)
  expect(screen.getByText('/admin/users')).toBeTruthy()
})
