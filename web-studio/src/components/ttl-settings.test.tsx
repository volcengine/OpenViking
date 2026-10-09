// @vitest-environment jsdom
import axios from 'axios'
import { useState } from 'react'
import type { InternalAxiosRequestConfig } from 'axios'
import { beforeEach, afterEach, expect, it, vi } from 'vitest'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createInstance } from 'i18next'
import { I18nextProvider } from 'react-i18next'
import { resources } from '#/i18n/resources'
import {
  InitialTtlSettings,
  LibraryTtlSettings,
  RootTtlSettings,
} from './ttl-settings'
import { TtlExpiry } from './ttl-expiry'
import { createTtlApi, isTtlRoot, ttlPolicyPatch } from '#/lib/ttl'
import type { TtlConfig } from '#/lib/ttl'
import { createAdminAccount } from '#/lib/admin'
import { normalizeFsEntry } from '#/routes/resources/-lib/normalize'
import type * as AppConnection from '#/hooks/use-app-connection'

const state = vi.hoisted(() => ({
  connection: {
    baseUrl: 'http://localhost:1933',
    accountId: 'acme',
    userId: 'alice',
    adminApiKey: 'test-control',
    apiKey: 'test-data',
  },
  connectionRole: 'admin',
  serverMode: 'api_key',
  isConnectionRoleLoading: false,
}))
vi.mock('#/hooks/use-app-connection', async (original) => ({
  ...(await original<typeof AppConnection>()),
  useAppConnection: () => state,
}))
let requests: InternalAxiosRequestConfig[]
let rejectPatch: boolean
let client: QueryClient
const i18n = createInstance()
const root = 'viking://user/alice/memories/events'
beforeEach(async () => {
  requests = []
  rejectPatch = false
  state.connection = {
    ...state.connection,
    accountId: 'acme',
    adminApiKey: 'test-control',
  }
  state.connectionRole = 'admin'
  state.serverMode = 'api_key'
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  await i18n.init({
    lng: 'en',
    resources,
    interpolation: { escapeValue: false },
  })
  const create = axios.create.bind(axios)
  vi.spyOn(axios, 'create').mockImplementation((options) =>
    create({
      ...options,
      adapter: async (config) => {
        requests.push(config)
        return {
          status: 200,
          statusText: 'OK',
          headers: {},
          config,
          data:
            rejectPatch && config.method === 'patch'
              ? {
                  status: 'error',
                  error: {
                    code: 'FAILED_PRECONDITION',
                    message: '1 directory failed; retry',
                    details: { failed_count: 1 },
                  },
                }
              : {
                  status: 'ok',
                  result: config.url?.includes('/content/ttl')
                    ? {
                        uri: root,
                        expires_at: null,
                        effective_policy: { mode: 'days', ttl_days: 7 },
                      }
                    : { settings: { ttl: {} } },
                },
        }
      },
    }),
  )
})
afterEach(() => {
  cleanup()
  client.clear()
  vi.restoreAllMocks()
})
function view(node = <LibraryTtlSettings />) {
  return (
    <I18nextProvider i18n={i18n}>
      <QueryClientProvider client={client}>{node}</QueryClientProvider>
    </I18nextProvider>
  )
}
function patches() {
  return requests.filter((r) => r.method === 'patch')
}
function select(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } })
}
async function days(value = '30') {
  await screen.findByLabelText('Library override')
  select('Library override', 'days')
  fireEvent.change(screen.getByLabelText('Retention days'), {
    target: { value },
  })
}
async function save() {
  fireEvent.click(screen.getByRole('button', { name: 'Save and apply' }))
  await screen.findByText('TTL policy applied.')
}

it('saves a sparse root override and distinguishes inherit, disable and removing the override', async () => {
  render(view())
  await screen.findByLabelText('Library override')
  select('Configuration scope', 'directory')
  await screen.findByText('Currently effective: 7 days')
  await days()
  await save()
  expect(JSON.parse(patches()[0].data)).toEqual({
    settings: {
      ttl: { directories: { [root]: { mode: 'days', ttl_days: 30 } } },
    },
  })
  expect(patches()[0].headers.get('X-API-Key')).toBe('test-control')
  for (const [mode, policy] of [
    ['disabled', { mode: 'disabled' }],
    ['inherit', { mode: 'inherit' }],
    ['server', null],
  ] as const) {
    select('Library override', mode)
    await save()
    expect(
      JSON.parse(patches().at(-1)!.data).settings.ttl.directories[root],
    ).toEqual(policy)
  }
})

it('limits the global form and rejects invalid days and child directory edits', async () => {
  render(view())
  await screen.findByLabelText('Library override')
  expect(
    screen.queryByRole('option', { name: 'Absolute expiration time' }),
  ).toBeNull()
  await days('0')
  expect(
    screen.getByRole<HTMLButtonElement>('button', {
      name: 'Save and apply',
    }).disabled,
  ).toBe(true)
  select('Configuration scope', 'directory')
  select('Root directory URI', `${root}/2026/10/08`)
  expect(screen.getByRole('alert').textContent).toContain('Individual sessions')
  expect(screen.queryByRole('button', { name: 'Save and apply' })).toBeNull()
  expect(() => ttlPolicyPatch(`${root}/2026`, { mode: 'disabled' })).toThrow()
  expect(isTtlRoot('viking://user/alice/peers/bob/memories/events/')).toBe(true)
  expect(isTtlRoot('viking://user/alice/sessions/s1')).toBe(false)
  expect(patches()).toHaveLength(0)
})

it('submits absolute expiry in Unix seconds and keeps partial failure retryable', async () => {
  render(view())
  await screen.findByLabelText('Library override')
  select('Configuration scope', 'sessions')
  select('Library override', 'absolute')
  const local = '2030-10-10T12:30:00'
  select('Expiration time (your local time zone)', local)
  rejectPatch = true
  fireEvent.click(screen.getByRole('button', { name: 'Save and apply' }))
  await screen.findByText('1 directory failed; retry')
  expect(screen.queryByText('TTL policy applied.')).toBeNull()
  rejectPatch = false
  await save()
  expect(patches()[1].data).toBe(patches()[0].data)
  expect(JSON.parse(patches()[1].data)).toEqual({
    settings: {
      ttl: {
        sessions: {
          mode: 'absolute',
          ttl_absolute: new Date(local).getTime() / 1000,
        },
      },
    },
  })
})

it('does not let an unsaved policy follow an account switch', async () => {
  const rendered = render(view())
  await days('90')
  state.connection = { ...state.connection, accountId: 'another' }
  rendered.rerender(view())
  await screen.findByLabelText('Library override')
  expect(
    screen.getByLabelText<HTMLSelectElement>('Library override').value,
  ).toBe('server')
  await days('2')
  await save()
  expect(patches()[0].url).toContain('/accounts/another/configuration')
  expect(JSON.parse(patches()[0].data)).toEqual({
    settings: { ttl: { global: { mode: 'days', ttl_days: 2 } } },
  })
})

it('gates management for ordinary users and dev mode like the existing admin API', () => {
  state.connectionRole = 'user'
  const rendered = render(view())
  expect(screen.queryByRole('button', { name: 'Save and apply' })).toBeNull()
  expect(requests).toHaveLength(0)
  state.serverMode = 'dev'
  state.connection = { ...state.connection, adminApiKey: '' }
  rendered.rerender(view())
  expect(screen.queryByRole('button', { name: 'Save and apply' })).toBeNull()
  expect(requests).toHaveLength(0)
})

it('keeps in-flight API calls bound to their original account and credentials', async () => {
  const original = { ...state.connection }
  const api = createTtlApi(original, true)
  original.accountId = 'other'
  original.apiKey = 'other-key'
  await api.setPolicy('global', { mode: 'days', ttl_days: 7 })
  await api.get(root)
  expect(requests[0].url).toContain('/accounts/acme/configuration')
  expect(requests[0].headers.get('X-API-Key')).toBe('test-control')
  expect(requests[1].headers.get('X-API-Key')).toBe('test-data')
  expect(requests[1].headers.get('X-OpenViking-Account')).toBe('acme')
})

it('shows server expiry in both languages, preserves null and omits unknown values', async () => {
  const entry = normalizeFsEntry(
    { uri: `${root}/2026/10/08/file.md`, expires_at: '2030-10-10T00:00:00Z' },
    root,
  )
  const rendered = render(view(<TtlExpiry expiresAt={entry.expiresAt} />))
  expect(rendered.container.querySelector('time')?.dateTime).toBe(
    entry.expiresAt,
  )
  await i18n.changeLanguage('zh-CN')
  await waitFor(() =>
    expect(rendered.container.textContent).toContain('到期时间'),
  )
  rendered.rerender(view(<TtlExpiry expiresAt={null} />))
  expect(rendered.container.textContent).toBe('不过期')
  rendered.rerender(view(<TtlExpiry />))
  expect(rendered.container.textContent).toBe('')
  rendered.rerender(view(<RootTtlSettings uri={`${root}/2026/10/08`} />))
  expect(rendered.container.textContent).toBe('')
  expect(
    normalizeFsEntry({ name: 's1', expires_at: null }, root).expiresAt,
  ).toBeNull()
})

it('includes initial TTL in the account creation request and allows server defaults', async () => {
  function CreationForm() {
    const [ttl, setTtl] = useState<TtlConfig>()
    return (
      <>
        <InitialTtlSettings value={ttl} onChange={setTtl} />
        <button
          onClick={() =>
            void createAdminAccount(
              { ...state.connection, apiKey: state.connection.adminApiKey },
              { accountId: 'new-library', adminUserId: 'alice', ttl },
            )
          }
        >
          {i18n.t('accountSwitcher:create')}
        </button>
      </>
    )
  }
  render(view(<CreationForm />))
  select('Initial library TTL', 'bestPractice')
  fireEvent.click(screen.getByText('Create account'))
  await waitFor(() => expect(requests).toHaveLength(1))
  expect(requests[0].url).toContain('/admin/accounts')
  expect(JSON.parse(requests[0].data)).toEqual({
    account_id: 'new-library',
    admin_user_id: 'alice',
    settings: {
      ttl: {
        global: { mode: 'disabled' },
        user_events: { mode: 'days', ttl_days: 60 },
        peer_events: { mode: 'days', ttl_days: 60 },
        sessions: { mode: 'days', ttl_days: 30 },
      },
    },
  })
  select('Initial library TTL', 'days')
  fireEvent.change(screen.getByLabelText('Retention days'), {
    target: { value: '0' },
  })
  expect(
    screen.getByLabelText<HTMLInputElement>('Retention days').validity.valid,
  ).toBe(false)
  select('Initial library TTL', 'server')
  fireEvent.click(screen.getByText('Create account'))
  await waitFor(() => expect(requests).toHaveLength(2))
  expect(JSON.parse(requests[1].data)).not.toHaveProperty('settings')
})
