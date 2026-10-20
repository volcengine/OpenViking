// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'
import { StudioSetup } from './studio-setup'

const mocks = vi.hoisted(() => ({
  verify: vi.fn(),
  complete: vi.fn(),
  language: vi.fn(),
  theme: vi.fn(),
}))
vi.mock('next-themes', () => ({
  useTheme: () => ({ theme: 'system', setTheme: mocks.theme }),
}))
vi.mock('react-i18next', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: {
      language: 'en',
      resolvedLanguage: 'en',
      changeLanguage: mocks.language,
    },
  }),
}))
vi.mock('#/lib/connection/onboarding', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  markStudioSetupPending: vi.fn(),
  verifyStudioSetup: mocks.verify,
  completeStudioSetup: mocks.complete,
}))
afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
})
beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
})

it('deduplicates identical saved connections and keeps the active profile', async () => {
  const connection = {
    serviceSelection: 'opensource',
    baseUrl: 'http://127.0.0.1:1933',
    apiKey: '',
    adminApiKey: '',
    accountId: 'default',
    userId: 'default',
  }
  localStorage.setItem(
    'ov_console_connection_profiles',
    JSON.stringify({
      activeId: 'second',
      profiles: [
        {
          id: 'first',
          name: 'Default',
          connection: { ...connection, userId: '' },
        },
        {
          id: 'second',
          name: 'Open source',
          connection: { ...connection, userId: '' },
        },
        {
          id: 'third',
          name: 'Remote',
          connection: { ...connection, baseUrl: 'https://remote.test/ov' },
        },
      ],
    }),
  )
  render(<StudioSetup onComplete={vi.fn()} />)
  const selector = screen.getByRole('combobox', {
    name: 'connectionChoice',
  })
  expect(selector.textContent).toContain('Open source')
  const user = userEvent.setup()
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  fireEvent.click(screen.getByRole('button', { name: 'advanced' }))
  expect(
    screen.getByLabelText<HTMLInputElement>('settings:fields.user').value,
  ).toBe('')
  fireEvent.click(screen.getByRole('button', { name: 'back' }))
  await user.click(screen.getByRole('combobox', { name: 'connectionChoice' }))
  expect(await screen.findAllByRole('option')).toHaveLength(3)
  expect(screen.getAllByRole('option', { name: 'Open source' })).toHaveLength(1)
  expect(screen.queryByRole('option', { name: 'Default' })).toBeNull()
  await user.click(screen.getByRole('option', { name: 'Remote' }))
  expect(
    screen.getByRole('combobox', { name: 'connectionChoice' }).textContent,
  ).toContain('Remote')
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  expect(screen.getAllByText('https://remote.test/ov').length).toBeGreaterThan(
    0,
  )
  fireEvent.click(screen.getByRole('button', { name: 'back' }))
  await user.click(screen.getByRole('combobox', { name: 'connectionChoice' }))
  await user.click(await screen.findByRole('option', { name: 'newConnection' }))
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: 'next' }).disabled,
  ).toBe(true)
  expect(
    screen.getByLabelText<HTMLInputElement>('settings:fields.baseUrl').value,
  ).toBe('')
})
it('shows the hosted server address on step one and requires a fresh API key each time', () => {
  localStorage.setItem(
    'ov_console_connection_profiles',
    JSON.stringify({
      activeId: 'hosted',
      profiles: [
        {
          id: 'hosted',
          name: 'Hosted',
          connection: {
            serviceSelection: 'volcengine',
            baseUrl: 'https://saved.hosted.test/ov',
            apiKey: 'saved-user-key',
            adminApiKey: 'saved-admin-key',
            accountId: 'default',
            userId: 'default',
          },
        },
      ],
    }),
  )
  const first = render(<StudioSetup onComplete={vi.fn()} />)
  const endpoint = screen.getByLabelText<HTMLInputElement>('hostedEndpoint')
  expect(endpoint.value).toBe('https://saved.hosted.test/ov')
  expect(
    screen.queryByRole('combobox', { name: 'connectionChoice' }),
  ).toBeNull()
  fireEvent.change(endpoint, {
    target: { value: 'https://another.hosted.test/ov' },
  })
  expect(endpoint.value).toBe('https://another.hosted.test/ov')
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  expect(
    screen.queryByRole('combobox', { name: 'connectionChoice' }),
  ).toBeNull()
  expect(screen.queryByLabelText('hostedEndpoint')).toBeNull()
  const key = screen.getByLabelText<HTMLInputElement>(
    'settings:fields.userApiKey',
  )
  expect(key.value).toBe('')
  expect(key.autocomplete).toBe('new-password')
  fireEvent.change(key, { target: { value: 'entered-key' } })
  first.unmount()

  render(<StudioSetup onComplete={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  expect(
    screen.getByLabelText<HTMLInputElement>('settings:fields.userApiKey').value,
  ).toBe('')
})
it('reuses an existing hosted profile only after the user reenters its API key', async () => {
  localStorage.setItem(
    'ov_console_connection_profiles',
    JSON.stringify({
      activeId: 'hosted',
      profiles: [
        {
          id: 'hosted',
          name: 'Hosted',
          connection: {
            serviceSelection: 'volcengine',
            baseUrl: 'https://saved.hosted.test/ov',
            apiKey: 'saved-user-key',
            adminApiKey: '',
            accountId: 'team',
            userId: 'alice',
          },
        },
      ],
    }),
  )
  mocks.verify.mockImplementation(async (draft) => draft)
  render(<StudioSetup onComplete={vi.fn()} />)
  expect(screen.getByLabelText<HTMLInputElement>('hostedEndpoint').value).toBe(
    'https://saved.hosted.test/ov',
  )
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  const key = screen.getByLabelText<HTMLInputElement>(
    'settings:fields.userApiKey',
  )
  expect(key.value).toBe('')
  fireEvent.change(key, { target: { value: 'saved-user-key' } })
  fireEvent.click(screen.getByRole('button', { name: 'verify' }))
  await screen.findByRole('status')
  fireEvent.click(screen.getByRole('button', { name: 'enter' }))
  expect(mocks.complete).toHaveBeenCalledWith(
    expect.objectContaining({ apiKey: 'saved-user-key' }),
    'studio:providers.volcengine',
    'hosted',
  )
})
it('accepts an older hosted profile without an API key after manual verification', async () => {
  localStorage.setItem(
    'ov_console_connection_profiles',
    JSON.stringify({
      activeId: 'legacy-hosted',
      profiles: [
        {
          id: 'legacy-hosted',
          name: 'Hosted',
          connection: {
            serviceSelection: 'volcengine',
            baseUrl: 'https://saved.hosted.test/ov',
            adminApiKey: '',
            accountId: 'default',
            userId: 'default',
          },
        },
      ],
    }),
  )
  const done = vi.fn()
  mocks.verify.mockImplementation(async (draft) => draft)
  render(<StudioSetup onComplete={done} />)
  expect(screen.getByLabelText<HTMLInputElement>('hostedEndpoint').value).toBe(
    'https://saved.hosted.test/ov',
  )
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  fireEvent.change(screen.getByLabelText('settings:fields.userApiKey'), {
    target: { value: 'newly-entered-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'verify' }))
  await screen.findByRole('status')
  fireEvent.click(screen.getByRole('button', { name: 'enter' }))
  expect(mocks.complete).toHaveBeenCalledWith(
    expect.objectContaining({ apiKey: 'newly-entered-key' }),
    'studio:providers.volcengine',
    undefined,
  )
  expect(done).toHaveBeenCalledOnce()
  expect(screen.queryByRole('alert')).toBeNull()
})
it('selects the configured open-source address when switching from hosted', () => {
  const hosted = {
    serviceSelection: 'volcengine',
    baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
    apiKey: 'hosted-key',
    adminApiKey: '',
    accountId: 'default',
    userId: 'default',
  }
  localStorage.setItem(
    'ov_console_connection_profiles',
    JSON.stringify({
      activeId: 'hosted',
      profiles: [
        { id: 'hosted', name: 'Hosted', connection: hosted },
        {
          id: 'opensource',
          name: 'Open source',
          connection: {
            ...hosted,
            serviceSelection: 'opensource',
            baseUrl: 'https://configured.example/ov',
          },
        },
      ],
    }),
  )
  render(<StudioSetup onComplete={vi.fn()} />)
  fireEvent.click(screen.getByLabelText('studio:providers.opensource'))
  expect(
    screen.getByRole('combobox', { name: 'connectionChoice' }).textContent,
  ).toContain('Open source')
  expect(
    screen.getAllByTitle('https://configured.example/ov').length,
  ).toBeGreaterThan(0)
  expect(screen.queryByLabelText('settings:fields.baseUrl')).toBeNull()
})

it('offers the local development service when only a hosted link is saved', () => {
  localStorage.setItem(
    'ov_console_connection_profiles',
    JSON.stringify({
      activeId: 'hosted',
      profiles: [
        {
          id: 'hosted',
          name: 'Hosted',
          connection: {
            serviceSelection: 'volcengine',
            baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
            apiKey: 'hosted-key',
            adminApiKey: '',
            accountId: 'default',
            userId: 'default',
          },
        },
      ],
    }),
  )
  render(<StudioSetup onComplete={vi.fn()} />)
  fireEvent.click(screen.getByLabelText('studio:providers.opensource'))
  expect(
    screen.getByRole('combobox', { name: 'connectionChoice' }).textContent,
  ).toContain('studio:localConnection')
  expect(screen.getAllByTitle('http://127.0.0.1:1933').length).toBeGreaterThan(
    0,
  )
  expect(screen.queryByLabelText('settings:fields.baseUrl')).toBeNull()
})
it('requires a valid new open-source address before continuing', () => {
  render(<StudioSetup onComplete={vi.fn()} />)
  const next = screen.getByRole<HTMLButtonElement>('button', { name: 'next' })
  expect(next.disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('settings:fields.baseUrl'), {
    target: { value: 'ftp://invalid.test' },
  })
  expect(next.disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('settings:fields.baseUrl'), {
    target: { value: 'http://service.test' },
  })
  expect(next.disabled).toBe(false)
  fireEvent.click(next)
  expect(screen.getByText('http://service.test')).toBeTruthy()
  expect(screen.queryByLabelText('settings:fields.baseUrl')).toBeNull()
})
it('combines preferences, applies changes immediately and retains connection fields when returning', () => {
  render(<StudioSetup onComplete={vi.fn()} />)
  fireEvent.click(screen.getByLabelText('中文'))
  expect(mocks.language).toHaveBeenCalledWith('zh-CN')
  fireEvent.click(screen.getByLabelText('dark'))
  expect(mocks.theme).toHaveBeenCalledWith('dark')
  fireEvent.change(screen.getByLabelText('settings:fields.baseUrl'), {
    target: { value: 'http://service.test' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  fireEvent.change(screen.getByLabelText('settings:fields.userApiKey'), {
    target: { value: 'synthetic-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'back' }))
  expect(
    screen.getByLabelText<HTMLInputElement>('settings:fields.baseUrl').value,
  ).toBe('http://service.test')
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  expect(
    screen.getByLabelText<HTMLInputElement>('settings:fields.userApiKey').value,
  ).toBe('synthetic-key')
})
it('keeps advanced fields out of keyboard navigation while collapsed and preserves their values', () => {
  render(<StudioSetup onComplete={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('settings:fields.baseUrl'), {
    target: { value: 'http://service.test' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  const toggle = screen.getByRole('button', { name: 'advanced' })
  const fields = document.getElementById('setup-advanced-fields')
  expect(toggle.getAttribute('aria-expanded')).toBe('false')
  expect(fields?.hasAttribute('inert')).toBe(true)
  fireEvent.click(toggle)
  expect(toggle.getAttribute('aria-expanded')).toBe('true')
  expect(fields?.hasAttribute('inert')).toBe(false)
  fireEvent.change(screen.getByLabelText('settings:fields.account'), {
    target: { value: 'team' },
  })
  fireEvent.click(toggle)
  expect(fields?.hasAttribute('inert')).toBe(true)
  fireEvent.click(toggle)
  expect(
    screen.getByLabelText<HTMLInputElement>('settings:fields.account').value,
  ).toBe('team')
})
it('keeps manual account and user fields without a user-directory action', () => {
  render(<StudioSetup onComplete={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('settings:fields.baseUrl'), {
    target: { value: 'http://service.test' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  fireEvent.click(screen.getByRole('button', { name: 'advanced' }))
  fireEvent.change(screen.getByLabelText('settings:fields.account'), {
    target: { value: 'work-memory' },
  })
  fireEvent.change(screen.getByLabelText('settings:fields.user'), {
    target: { value: 'wangladi' },
  })
  expect(screen.queryByRole('button', { name: 'loadUsers' })).toBeNull()
  expect(screen.queryByRole('combobox', { name: 'chooseUser' })).toBeNull()
  expect(
    screen.getByLabelText<HTMLInputElement>('settings:fields.user').value,
  ).toBe('wangladi')
})
it('enters only after verification, invalidates verification when fields change, and persists on explicit entry', async () => {
  const done = vi.fn()
  mocks.verify.mockImplementation(async (draft) => draft)
  render(<StudioSetup onComplete={done} />)
  fireEvent.change(screen.getByLabelText('settings:fields.baseUrl'), {
    target: { value: 'http://service.test' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  fireEvent.click(screen.getByRole('button', { name: 'verify' }))
  await screen.findByRole('status')
  expect(done).not.toHaveBeenCalled()
  expect(mocks.complete).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('settings:fields.userApiKey'), {
    target: { value: 'changed' },
  })
  expect(screen.queryByRole('status')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'verify' }))
  await screen.findByRole('status')
  fireEvent.click(screen.getByRole('button', { name: 'enter' }))
  expect(mocks.complete).toHaveBeenCalledOnce()
  expect(done).toHaveBeenCalledOnce()
})
it('keeps the user in setup on verification failure', async () => {
  mocks.verify.mockRejectedValueOnce(new Error('Forbidden'))
  const done = vi.fn()
  render(<StudioSetup onComplete={done} />)
  fireEvent.change(screen.getByLabelText('settings:fields.baseUrl'), {
    target: { value: 'http://service.test' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  fireEvent.click(screen.getByRole('button', { name: 'verify' }))
  await waitFor(() =>
    expect(screen.getByRole('alert').textContent).toContain('Forbidden'),
  )
  expect(done).not.toHaveBeenCalled()
})
it('edits the hosted endpoint and uses it for connection verification', async () => {
  render(<StudioSetup onComplete={vi.fn()} />)
  fireEvent.click(screen.getByLabelText('studio:providers.volcengine'))
  expect(
    screen.queryByRole('combobox', { name: 'connectionChoice' }),
  ).toBeNull()
  const endpoint = screen.getByRole<HTMLInputElement>('textbox', {
    name: 'hostedEndpoint',
  })
  expect(endpoint.value).toBe(
    'https://api.vikingdb.cn-beijing.volces.com/openviking',
  )
  fireEvent.change(endpoint, { target: { value: 'invalid-address' } })
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: 'next' }).disabled,
  ).toBe(true)
  fireEvent.change(endpoint, { target: { value: 'https://hosted.test/ov' } })
  expect(endpoint.value).toBe('https://hosted.test/ov')
  expect(
    screen.getByRole<HTMLButtonElement>('button', { name: 'next' }).disabled,
  ).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  expect(
    screen.queryByRole('combobox', { name: 'connectionChoice' }),
  ).toBeNull()
  expect(screen.queryByLabelText('hostedEndpoint')).toBeNull()
  expect(screen.getByText('hostedKeyHelp')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'verify' }))
  expect(screen.getByRole('alert').textContent).toBe('keyRequired')
  expect(document.activeElement).toBe(
    screen.getByLabelText('settings:fields.userApiKey'),
  )
  expect(mocks.verify).not.toHaveBeenCalled()
  fireEvent.change(screen.getByLabelText('settings:fields.userApiKey'), {
    target: { value: 'hosted-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'verify' }))
  await waitFor(() =>
    expect(mocks.verify).toHaveBeenCalledWith(
      expect.objectContaining({
        baseUrl: 'https://hosted.test/ov',
        apiKey: 'hosted-key',
      }),
      expect.any(AbortSignal),
    ),
  )
})
it('clears the hosted API key when its service address changes', () => {
  render(<StudioSetup onComplete={vi.fn()} />)
  fireEvent.click(screen.getByLabelText('studio:providers.volcengine'))
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  fireEvent.change(screen.getByLabelText('settings:fields.userApiKey'), {
    target: { value: 'old-service-secret' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'back' }))
  fireEvent.change(screen.getByLabelText('hostedEndpoint'), {
    target: { value: 'https://another.hosted.test/ov' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  expect(
    screen.getByLabelText<HTMLInputElement>('settings:fields.userApiKey').value,
  ).toBe('')
  fireEvent.click(screen.getByRole('button', { name: 'verify' }))
  expect(screen.getByRole('alert').textContent).toBe('keyRequired')
  expect(mocks.verify).not.toHaveBeenCalled()
})
it('keeps each workspace draft isolated when switching on the first step', () => {
  render(<StudioSetup onComplete={vi.fn()} />)
  const url = () =>
    screen.getByLabelText<HTMLInputElement>('settings:fields.baseUrl')
  const key = () =>
    screen.getByLabelText<HTMLInputElement>('settings:fields.userApiKey')
  fireEvent.change(url(), { target: { value: 'http://oss.test' } })
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  fireEvent.change(key(), { target: { value: 'synthetic-oss' } })
  fireEvent.click(screen.getByRole('button', { name: 'back' }))
  fireEvent.click(screen.getByLabelText('studio:providers.volcengine'))
  expect(screen.getByLabelText<HTMLInputElement>('hostedEndpoint').value).toBe(
    'https://api.vikingdb.cn-beijing.volces.com/openviking',
  )
  fireEvent.change(screen.getByLabelText('hostedEndpoint'), {
    target: { value: 'https://cloud.test/ov' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  expect(screen.queryByLabelText('hostedEndpoint')).toBeNull()
  expect(key().value).toBe('')
  fireEvent.change(key(), { target: { value: 'synthetic-cloud' } })
  fireEvent.click(screen.getByRole('button', { name: 'back' }))
  fireEvent.click(screen.getByLabelText('studio:providers.opensource'))
  expect(url().value).toBe('http://oss.test')
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  expect(key().value).toBe('synthetic-oss')
  fireEvent.click(screen.getByRole('button', { name: 'back' }))
  fireEvent.click(screen.getByLabelText('studio:providers.volcengine'))
  expect(screen.getByLabelText<HTMLInputElement>('hostedEndpoint').value).toBe(
    'https://cloud.test/ov',
  )
  fireEvent.click(screen.getByRole('button', { name: 'next' }))
  expect(key().value).toBe('synthetic-cloud')
})

it('loads the logo from the deployment base path', () => {
  vi.stubEnv('BASE_URL', '/studio/')
  const { container } = render(<StudioSetup onComplete={vi.fn()} />)
  expect(container.querySelector('img')?.getAttribute('src')).toBe(
    '/studio/ov-logo.png',
  )
})
