// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryTemplatesPage } from './memory-templates-page'
import type * as MemoryTemplatesModule from '#/lib/memory-templates'

const api = vi.hoisted(() => ({
  fetch: vi.fn(),
  save: vi.fn(),
  reset: vi.fn(),
}))
vi.mock('#/lib/memory-templates', async (importOriginal) => ({
  ...(await importOriginal<typeof MemoryTemplatesModule>()),
  fetchMemoryTemplates: api.fetch,
  updateMemoryTemplate: api.save,
  resetMemoryTemplate: api.reset,
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      accountId: 'acme',
      adminApiKey: 'admin-key',
      baseUrl: 'http://localhost',
      userId: 'admin',
    },
    connectionRole: 'admin',
    isConnectionRoleLoading: false,
    serverMode: 'api_key',
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const schema = {
  memory_type: 'profile',
  description: 'Original rule',
  directory: 'viking://user/{{ user_space }}/memories',
  filename_template: 'profile.md',
  enabled: true,
  fields: [{ name: 'content', description: 'Profile body', merge_op: 'patch' }],
}

afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

it('publishes edited instructions with the locked schema values intact', async () => {
  api.fetch.mockResolvedValue({
    account_id: 'acme',
    templates: [
      {
        memory_type: 'profile',
        status: 'system_default',
        updated_at: null,
        defaults: schema,
        effective: schema,
      },
    ],
  })
  api.save.mockResolvedValue({})
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <MemoryTemplatesPage />
    </QueryClientProvider>,
  )

  fireEvent.click(
    (
      await screen.findAllByRole('button', { name: /memoryTemplates.fileName/ })
    )[0],
  )
  fireEvent.click(screen.getByRole('button', { name: 'memoryTemplates.edit' }))
  fireEvent.change(
    screen.getByRole('textbox', { name: 'memoryTemplates.typeDescription' }),
    {
      target: { value: 'New extraction rule' },
    },
  )
  fireEvent.click(screen.getByRole('button', { name: 'memoryTemplates.save' }))

  await waitFor(() => {
    expect(api.save).toHaveBeenCalledWith(
      expect.objectContaining({ accountId: 'acme', apiKey: 'admin-key' }),
      'profile',
      {
        ...schema,
        description: 'New extraction rule',
      },
    )
  })
})

it('shows the path pattern returned by the server', async () => {
  const eventSchema = {
    ...schema,
    memory_type: 'events',
    directory: 'viking://user/{{ user_space }}/memories/events',
    filename_template: '{{ year }}/{{ month }}/{{ day }}/{{ event_name }}.md',
  }
  api.fetch.mockResolvedValue({
    account_id: 'acme',
    templates: [
      {
        memory_type: 'events',
        status: 'system_default',
        updated_at: null,
        defaults: eventSchema,
        effective: eventSchema,
      },
    ],
  })
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <MemoryTemplatesPage />
    </QueryClientProvider>,
  )

  fireEvent.click(
    (
      await screen.findAllByRole('button', { name: /memoryTemplates.fileName/ })
    )[3],
  )
  expect(
    screen.getByText(
      'viking://user/{{ user_space }}/memories/events/{{ year }}/{{ month }}/{{ day }}/{{ event_name }}.md',
    ),
  ).toBeTruthy()
})
