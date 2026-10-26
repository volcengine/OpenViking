// @vitest-environment jsdom
import type { ComponentType } from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { Route } from './index'

const state = vi.hoisted(() => ({
  isGatewayHost: false,
  provider: 'opensource',
}))
vi.mock('#/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: state.provider }),
}))
vi.mock('#/hooks/use-app-connection', () => ({ useAppConnection: () => state }))
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<object>()),
  Navigate: ({ to }: { to: string }) => <output>{to}</output>,
}))
afterEach(cleanup)
it.each([false, true])(
  'routes the host home appropriately: gateway=%s',
  async (isGatewayHost) => {
    state.isGatewayHost = isGatewayHost
    const Index = Route.options.component as ComponentType & {
      preload?: () => Promise<unknown>
    }
    await Index.preload?.()
    render(<Index />)
    expect(screen.getByText(isGatewayHost ? '/gateway' : '/home')).toBeTruthy()
  },
)

it('routes hosted OpenViking connections to the filesystem', async () => {
  state.isGatewayHost = false
  state.provider = 'volcengine'
  const Index = Route.options.component as ComponentType & {
    preload?: () => Promise<unknown>
  }
  await Index.preload?.()
  render(<Index />)
  expect(screen.getByText('/filesystem')).toBeTruthy()
})
