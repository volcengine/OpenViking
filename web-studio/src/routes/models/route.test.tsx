// @vitest-environment jsdom

import type { ComponentType, ReactNode } from 'react'
import type * as TanStackRouter from '@tanstack/react-router'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { Route } from './route'

const mocks = vi.hoisted(() => ({
  fetch: vi.fn(),
  patch: vi.fn(),
  role: 'root' as 'admin' | 'root',
}))

vi.mock('#/lib/admin', () => ({
  fetchAccountModelConfiguration: mocks.fetch,
  patchAccountModelConfiguration: mocks.patch,
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      accountId: 'acme',
      adminApiKey: 'root-key',
      apiKey: '',
      baseUrl: 'http://localhost:1933',
      userId: 'root',
    },
    connectionRole: mocks.role,
    identityScopeKey: 'acme:root',
    isConnectionRoleLoading: false,
    serverMode: 'api_key',
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn() } }))
vi.mock('@tanstack/react-router', async (importOriginal) => {
  const original = await importOriginal<typeof TanStackRouter>()
  return {
    ...original,
    Link: ({ children }: { children: ReactNode }) => children,
  }
})

beforeEach(() => {
  mocks.role = 'root'
  mocks.fetch.mockReset()
  mocks.patch.mockReset()
})
afterEach(cleanup)

async function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const AccountModelsPage = Route.options.component as ComponentType & {
    preload?: () => Promise<unknown>
  }
  await AccountModelsPage.preload?.()
  const view = render(
    <QueryClientProvider client={client}>
      <AccountModelsPage />
    </QueryClientProvider>,
  )
  return { ...view, user: userEvent.setup() }
}

it('renders absent model sections as inherited from Cluster', async () => {
  mocks.fetch.mockResolvedValue({ account_id: 'acme', settings: {} })
  await mount()

  expect(await screen.findAllByText('models.state.inherited')).toHaveLength(2)
  expect(screen.getAllByText('models.actions.configure')).toHaveLength(2)
})

it('keeps loaded keys masked and submits the complete credential array', async () => {
  const settings = {
    vlm: {
      model: 'vision-default',
      timeout: 45,
      credentials: [
        {
          id: 'primary',
          provider: 'openai',
          api_key: 'secret-primary',
        },
        {
          id: 'backup',
          provider: 'litellm',
          api_key: 'secret-backup',
        },
      ],
    },
  }
  mocks.fetch.mockResolvedValue({ account_id: 'acme', settings })
  mocks.patch.mockResolvedValue({ account_id: 'acme', settings })
  const { container, user } = await mount()

  await user.click(await screen.findByText('models.actions.edit'))
  const secretInputs = Array.from(
    document.querySelectorAll<HTMLInputElement>('input[type="password"]'),
  )
  expect(secretInputs.map((input) => input.value)).toEqual([
    'secret-primary',
    'secret-backup',
  ])
  expect(container.textContent).not.toContain('secret-primary')
  expect(container.textContent).not.toContain('secret-backup')

  const modelInput = screen.getByLabelText('models.fields.model')
  await user.clear(modelInput)
  await user.type(modelInput, 'vision-updated')
  await user.click(screen.getByText('models.actions.save'))

  await waitFor(() => expect(mocks.patch).toHaveBeenCalledTimes(1))
  const patch = mocks.patch.mock.calls[0][2]
  expect(patch.vlm.model).toBe('vision-updated')
  expect(patch.vlm.credentials).toHaveLength(2)
  expect(
    patch.vlm.credentials.map((item: { api_key?: string }) => item.api_key),
  ).toEqual(['secret-primary', 'secret-backup'])
  expect(JSON.stringify(patch)).not.toContain('••')
})

it('hides remaining keys after a revealed credential is removed', async () => {
  const settings = {
    vlm: {
      model: 'vision-default',
      credentials: [
        { provider: 'openai', api_key: 'secret-primary' },
        { provider: 'litellm', api_key: 'secret-backup' },
      ],
    },
  }
  mocks.fetch.mockResolvedValue({ account_id: 'acme', settings })
  const { user } = await mount()

  await user.click(await screen.findByText('models.actions.edit'))
  const revealButtons = screen.getAllByLabelText('models.actions.revealApiKey')
  await user.click(revealButtons[0])
  expect(screen.getByDisplayValue('secret-primary').getAttribute('type')).toBe(
    'text',
  )

  await user.click(
    screen.getAllByLabelText('models.actions.removeCredential')[0],
  )

  expect(screen.queryByDisplayValue('secret-primary')).toBeNull()
  expect(screen.getByDisplayValue('secret-backup').getAttribute('type')).toBe(
    'password',
  )
})

it('resets one explicit section with a section-level null', async () => {
  const settings = {
    query_planner: {
      model: 'planner',
      credentials: [{ provider: 'openai', api_key: 'secret' }],
    },
  }
  mocks.fetch.mockResolvedValue({ account_id: 'acme', settings })
  mocks.patch.mockResolvedValue({ account_id: 'acme', settings: {} })
  const { user } = await mount()

  await user.click(await screen.findByText('models.actions.inherit'))
  await user.click(screen.getByText('models.actions.confirmInherit'))

  await waitFor(() =>
    expect(mocks.patch).toHaveBeenCalledWith(expect.anything(), 'acme', {
      query_planner: null,
    }),
  )
})

it('does not load sensitive settings for an Account ADMIN', async () => {
  mocks.role = 'admin'
  await mount()

  expect(await screen.findByText('models.accessDenied.title')).toBeTruthy()
  expect(mocks.fetch).not.toHaveBeenCalled()
})
