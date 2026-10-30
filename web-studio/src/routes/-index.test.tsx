// @vitest-environment jsdom
import { Route } from '#/routes/index'
import { cleanup, render, screen } from '@testing-library/react'
import type { ComponentType } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  isGatewayHost: false,
  provider: 'opensource',
  connection: {
    baseUrl: 'http://service.test',
    apiKey: 'user-key',
    adminApiKey: '',
    accountId: 'default',
    userId: 'alice',
  },
  connectionRole: 'user',
  isConnectionRoleLoading: false,
  serverMode: 'api_key',
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: state.provider }),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => state,
}))
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
    expect(
      screen.getByText(isGatewayHost ? '/admin/gateway' : '/home'),
    ).toBeTruthy()
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
