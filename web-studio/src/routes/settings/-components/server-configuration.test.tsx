// @vitest-environment jsdom
import {
  cleanup,
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'
import { ServerConfigurationEditor } from './server-configuration'
import { Route } from '../route'
import zh from '#/i18n/locales/zh-CN/workspace'
import en from '#/i18n/locales/en/workspace'
import type { ModelConfig } from '../-lib/config-file-api'

const state = vi.hoisted(() => ({
  role: 'root',
  get: vi.fn(),
  save: vi.fn(),
  preview: vi.fn(),
  restart: vi.fn(),
  copy: vi.fn(),
}))
vi.mock('#/components/code-editor', () => ({
  CodeEditor: ({
    initialContent,
    ariaLabel,
    readOnly,
    onChange,
  }: {
    initialContent: string
    ariaLabel: string
    readOnly: boolean
    onChange: (content: string) => void
  }) => (
    <textarea
      aria-label={ariaLabel}
      value={initialContent}
      readOnly={readOnly}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))
vi.mock('#/lib/clipboard', () => ({ copyTextToClipboard: state.copy }))
vi.mock('../-lib/config-file-api', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  createConfigFileApi: () => ({
    get: state.get,
    save: state.save,
    preview: state.preview,
    restart: state.restart,
  }),
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      accountId: 'default',
      userId: 'default',
      baseUrl: '',
      adminApiKey: 'root',
      apiKey: '',
    },
    saveConnection: vi.fn(),
    connectionRole: state.role,
    identityScopeKey: 'default',
    serverMode: 'api_key',
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: 'en' },
  }),
}))
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
  state.role = 'root'
})
const language = {
  model: 'default-model',
  timeout: 60,
  credentials: [
    {
      id: 'a',
      provider: 'openai',
      model: 'model-a',
      api_key: 'secret-a',
      api_base: 'https://a.example.com',
      extra_headers: { keep: 'yes' },
      max_tokens: 2048,
      extra_request_body: { keep: true },
    },
    {
      id: 'b',
      provider: 'azure',
      model: 'model-b',
      api_key: 'secret-b',
      api_base: 'https://b.example.com',
      api_version: 'v1',
    },
  ],
}
const rerank = {
  provider: 'jev',
  model: 'jev-latest',
  api_key: 'jev-secret',
  api_base: 'https://api.typesafe.ai',
  mode: 'noul',
  threshold: 0.1,
  timeout: 30,
  max_input_tokens: 0,
  extra_headers: { keep: 'yes' },
  log_payloads: false,
}
const data = {
  file_path: '/server/ov.conf',
  writable: true,
  restart_required: false,
  restart: { supported: true, instance_id: 'new', restarting: false },
  revision: 'revision',
  models: {
    vlm: { config: language },
    embedding: {
      config: {
        max_retries: 3,
        max_concurrent: 10,
        dense: {
          model: 'embedding-model',
          dimension: 1024,
          input: 'text',
          credentials: [
            {
              id: 'embed',
              provider: 'openai',
              api_key: 'embedding-key',
              api_base: 'https://e.example.com',
            },
          ],
        },
      },
    },
  },
}
const file = {
  vlm: language,
  embedding: data.models.embedding.config,
  rerank,
  server: { port: 1933, root_api_key: '${ROOT_KEY}' },
  storage: { workspace: '/server/data' },
}
const content = JSON.stringify(file, null, 2)
async function apply() {
  await act(async () => {
    fireEvent.click(screen.getByText('models.apply'))
  })
}
function mount(
  payload: typeof data & {
    overrides?: { cluster: string[]; account: string[] }
  } = data,
  withSettingsTabs = false,
) {
  state.get.mockResolvedValue({ ...structuredClone(payload), content })
  state.preview.mockImplementation(async (text: string, changes = {}) => {
    const raw = { ...JSON.parse(text), ...changes }
    return {
      content: Object.keys(changes).length
        ? JSON.stringify(raw, null, 2)
        : text,
      models: Object.fromEntries(
        Object.entries(payload.models).map(([kind, entry]) => [
          kind,
          {
            ...entry,
            config: raw[kind] ?? entry.config,
          },
        ]),
      ),
    }
  })
  state.save.mockResolvedValue({ revision: 'saved-revision' })
  state.restart.mockResolvedValue({
    supported: true,
    instance_id: 'old',
    restarting: true,
  })
  state.copy.mockResolvedValue(undefined)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const SettingsRoute = Route.options.component!
  render(
    <QueryClientProvider client={client}>
      {withSettingsTabs ? <SettingsRoute /> : <ServerConfigurationEditor />}
    </QueryClientProvider>,
  )
  return client
}
async function menuAction(
  region: ReturnType<typeof within>,
  index: number,
  name: string,
) {
  fireEvent.click(region.getAllByRole('button', { name: 'models.more' })[index])
  await act(async () => {
    fireEvent.click(await screen.findByRole('menuitem', { name }))
  })
}
function section(name: string) {
  return within(screen.getByRole('region', { name: `models.${name}` }))
}
it('includes localized contract labels in model settings', () => {
  expect(zh.settings.models.fields.dimension).toBe('向量维度')
  expect(en.settings.models.fields.dimension).toBe('Dimensions')
})
it.each([false, true])(
  'keeps the original revision across background refresh (applied: %s)',
  async (applied) => {
    const client = mount()
    await screen.findByText('model-a')
    fireEvent.click(
      section('vlmType').getAllByRole('button', { name: 'models.edit' })[0],
    )
    fireEvent.change(screen.getByLabelText('models.fields.model'), {
      target: { value: 'draft-model' },
    })
    if (applied) await apply()
    const refreshed = structuredClone(data)
    refreshed.revision = 'external-revision'
    refreshed.models.vlm.config.timeout = 99
    state.get.mockResolvedValue({
      ...refreshed,
      content: JSON.stringify({ ...file, vlm: refreshed.models.vlm.config }),
    })
    await client.refetchQueries({
      queryKey: ['server-configuration', 'default'],
    })
    if (!applied) await apply()
    fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
    await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1))
    expect(state.save.mock.calls[0][1]).toBe('revision')
    expect(JSON.parse(state.save.mock.calls[0][0]).vlm.timeout).toBe(60)
  },
)
it('allows keyless OpenAI-compatible embedding endpoints but requires an address', async () => {
  mount()
  await screen.findByText('model-a')
  fireEvent.click(
    section('embeddingType').getByRole('button', { name: 'models.edit' }),
  )
  const key = screen.getByLabelText('models.fields.api_key')
  expect(key.hasAttribute('required')).toBe(false)
  fireEvent.change(key, { target: { value: '' } })
  const base = screen.getByLabelText('models.fields.api_base')
  expect(base.hasAttribute('required')).toBe(true)
  fireEvent.change(base, { target: { value: 'http://localhost:8000/v1' } })
  await apply()
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1))
  expect(
    JSON.parse(state.save.mock.calls[0][0]).embedding.dense.credentials[0],
  ).toMatchObject({ api_key: null, api_base: 'http://localhost:8000/v1' })
})
it('shows file scope, restart and override warnings and blocks saves to read-only files', async () => {
  mount({
    ...structuredClone(data),
    writable: false,
    restart_required: true,
    overrides: { cluster: [], account: ['vlm'] },
  })
  await screen.findByText('/server/ov.conf')
  expect(screen.getByText('models.restartRequired')).toBeTruthy()
  expect(screen.getByText('models.fileReadOnly')).toBeTruthy()
  expect(screen.getByText(/models.overrideWarning/).textContent).toContain(
    'default.vlm',
  )
  const vlm = section('vlmType')
  await menuAction(vlm, 0, 'models.moveDown')
  expect(
    screen
      .getByRole('button', { name: 'models.saveAll' })
      .hasAttribute('disabled'),
  ).toBe(true)
  expect(state.save).not.toHaveBeenCalled()
})
it('stages additions and confirmed deletions, cancels edits and undoes the draft', async () => {
  mount()
  await screen.findByText('model-a')
  const vlm = section('vlmType')
  fireEvent.click(vlm.getByRole('button', { name: 'models.addModel' }))
  fireEvent.change(screen.getByLabelText('models.fields.model'), {
    target: { value: 'new-model' },
  })
  fireEvent.change(screen.getByLabelText('models.fields.api_key'), {
    target: { value: 'new-key' },
  })
  await apply()
  expect(vlm.getByText('new-model')).toBeTruthy()
  expect(state.save).not.toHaveBeenCalled()
  await menuAction(vlm, 2, 'models.remove')
  fireEvent.click(
    within(screen.getByRole('dialog')).getByRole('button', {
      name: 'models.dismiss',
    }),
  )
  expect(vlm.getByText('new-model')).toBeTruthy()
  await menuAction(vlm, 2, 'models.remove')
  await act(async () => {
    fireEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', {
        name: 'models.remove',
      }),
    )
  })
  expect(vlm.queryByText('new-model')).toBeNull()
  fireEvent.click(vlm.getAllByRole('button', { name: 'models.edit' })[0])
  fireEvent.change(screen.getByLabelText('models.fields.model'), {
    target: { value: 'discarded' },
  })
  fireEvent.click(screen.getByText('models.dismiss'))
  expect(vlm.queryByText('discarded')).toBeNull()
  expect(screen.queryByRole('button', { name: 'models.saveAll' })).toBeNull()
  await menuAction(vlm, 0, 'models.moveDown')
  fireEvent.click(screen.getByRole('button', { name: 'models.discardAll' }))
  expect(vlm.queryByText('models.unsaved')).toBeNull()
})
it('does not fetch any model credentials without ROOT permission', () => {
  state.role = 'admin'
  mount()
  expect(screen.getByText('models.rootRequired')).toBeTruthy()
  expect(state.get).not.toHaveBeenCalled()
})
it('shows only VLM and Embedding even when other models are configured', async () => {
  mount()
  await screen.findByText('model-a')
  expect(screen.getAllByRole('region')).toHaveLength(2)
  expect(
    screen.queryByRole('region', { name: 'models.plannerType' }),
  ).toBeNull()
  expect(screen.queryByRole('region', { name: 'models.rerankType' })).toBeNull()
  expect(screen.queryByText('jev-secret')).toBeNull()
  expect(section('embeddingType').getByText(/1024/)).toBeTruthy()
})
it('copies a model ID from its menu without touching configuration', async () => {
  mount()
  await screen.findByText('model-a')
  await menuAction(section('vlmType'), 0, 'models.copyModelId')
  expect(state.copy).toHaveBeenCalledWith('model-a')
  expect(state.save).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: 'models.saveAll' })).toBeNull()
})
it('adds an embedding credential without changing its model contract', async () => {
  mount()
  await screen.findByText('model-a')
  fireEvent.click(
    section('embeddingType').getByRole('button', { name: 'models.addModel' }),
  )
  fireEvent.change(screen.getByLabelText('models.fields.api_key'), {
    target: { value: 'new-embedding-key' },
  })
  await apply()
  expect(
    section('embeddingType').getAllByRole('button', { name: 'models.edit' }),
  ).toHaveLength(2)
})
it('offers the target vector group when adding to multiple embedding groups', async () => {
  const payload = structuredClone(data)
  Object.assign(payload.models.embedding.config, {
    sparse: {
      model: 'sparse-model',
      credentials: [{ provider: 'local', model: 'sparse-model' }],
    },
  })
  mount(payload)
  await screen.findByText('sparse-model')
  const embedding = section('embeddingType')
  expect(
    embedding.getAllByRole('button', { name: 'models.addModel' }),
  ).toHaveLength(1)
  await userEvent.click(
    embedding.getByRole('button', { name: 'models.addModel' }),
  )
  await userEvent.click(
    await screen.findByRole('menuitem', { name: 'models.sparse' }),
  )
  fireEvent.change(screen.getByLabelText('models.fields.api_key'), {
    target: { value: 'sparse-key' },
  })
  await apply()
  expect(
    embedding.getAllByRole('button', { name: 'models.edit' }),
  ).toHaveLength(3)
})
it.each(['vlmType', 'embeddingType'])(
  'supports a custom OpenAI-compatible endpoint for %s',
  async (kind) => {
    mount()
    await screen.findByText('model-a')
    const index = kind === 'vlmType' ? 1 : 0
    fireEvent.click(
      section(kind).getAllByRole('button', { name: 'models.edit' })[index],
    )
    fireEvent.click(screen.getByLabelText('models.fields.provider'))
    await userEvent.click(
      await screen.findByRole('option', { name: 'models.customProvider' }),
    )
    if (kind === 'vlmType')
      await waitFor(() =>
        expect(screen.queryByLabelText('models.fields.api_version')).toBeNull(),
      )
    const base = screen.getByLabelText('models.fields.api_base')
    expect(base.hasAttribute('required')).toBe(true)
    fireEvent.change(base, {
      target: { value: 'https://gateway.example.com/v1' },
    })
    fireEvent.change(screen.getByLabelText('models.fields.api_key'), {
      target: { value: 'custom-secret' },
    })
    await apply()
    expect(
      section(kind).getByText('https://gateway.example.com/v1'),
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
    await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1))
    const changes = JSON.parse(state.save.mock.calls[0][0])
    const credentials =
      kind === 'vlmType'
        ? changes.vlm.credentials
        : changes.embedding.dense.credentials
    expect(credentials[index]).toMatchObject({
      provider: 'openai',
      api_base: 'https://gateway.example.com/v1',
      api_key: 'custom-secret',
    })
    expect(JSON.stringify(changes)).not.toContain('__custom_openai')
  },
)
it('saves visible categories together without altering hidden model settings', async () => {
  mount()
  await screen.findByText('model-a')
  expect(
    section('vlmType').getByText('models.preferred').parentElement?.textContent,
  ).toContain('model-a')
  await menuAction(section('vlmType'), 0, 'models.moveDown')
  expect(
    section('vlmType').getByText('models.backup').parentElement?.textContent,
  ).toContain('model-a')
  fireEvent.click(
    section('embeddingType').getByRole('button', { name: 'models.parameters' }),
  )
  fireEvent.change(screen.getByLabelText('models.fields.max_retries'), {
    target: { value: '5' },
  })
  await apply()
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
  await waitFor(() =>
    expect(JSON.parse(state.save.mock.calls[0][0])).toEqual({
      ...file,
      vlm: {
        ...language,
        credentials: [language.credentials[1], language.credentials[0]],
      },
      embedding: { ...data.models.embedding.config, max_retries: 5 },
    }),
  )
  expect(state.save).toHaveBeenCalledTimes(1)
})
it('keeps readonly viewing and edit transition separate from applying and saving', async () => {
  mount()
  await screen.findByText('model-a')
  fireEvent.click(
    section('vlmType').getAllByRole('button', { name: 'models.view' })[0],
  )
  let dialog = within(screen.getByRole('dialog'))
  expect(
    dialog.getByLabelText<HTMLInputElement>('models.fields.api_key').readOnly,
  ).toBe(true)
  fireEvent.click(dialog.getByRole('button', { name: 'models.edit' }))
  dialog = within(screen.getByRole('dialog'))
  expect(
    dialog.getByLabelText<HTMLInputElement>('models.fields.api_key').readOnly,
  ).toBe(false)
  expect(screen.queryByText('models.unsaved')).toBeNull()
  expect(state.save).not.toHaveBeenCalled()
})
it.each(['vlmType', 'embeddingType'])(
  'shows the inherited model ID in view and edit without creating an override for %s',
  async (kind) => {
    const payload = structuredClone(data)
    const config =
      kind === 'vlmType'
        ? payload.models.vlm.config
        : payload.models.embedding.config.dense
    delete (config.credentials[0] as { model?: string }).model
    mount(payload)
    await screen.findByText(config.model)
    fireEvent.click(
      section(kind).getAllByRole('button', { name: 'models.view' })[0],
    )
    let dialog = within(screen.getByRole('dialog'))
    const model = dialog.getByLabelText<HTMLInputElement>('models.fields.model')
    expect(model.value).toBe(config.model)
    expect(model.readOnly).toBe(true)
    fireEvent.click(dialog.getByRole('button', { name: 'models.edit' }))
    dialog = within(screen.getByRole('dialog'))
    expect(
      dialog.getByLabelText<HTMLInputElement>('models.fields.model').value,
    ).toBe(config.model)
    fireEvent.change(dialog.getByLabelText('models.fields.api_key'), {
      target: { value: 'rotated-key' },
    })
    await apply()
    const changes = state.preview.mock.calls[0][1]
    const binding =
      kind === 'vlmType'
        ? changes.vlm.credentials[0]
        : changes.embedding.dense.credentials[0]
    expect(binding.model).toBeUndefined()
    expect(binding.api_key).toBe('rotated-key')

    fireEvent.click(
      section(kind).getAllByRole('button', { name: 'models.edit' })[0],
    )
    fireEvent.change(screen.getByLabelText('models.fields.model'), {
      target: { value: 'explicit-model' },
    })
    await apply()
    const updated = state.preview.mock.lastCall![1]
    expect(
      (kind === 'vlmType'
        ? updated.vlm.credentials[0]
        : updated.embedding.dense.credentials[0]
      ).model,
    ).toBe('explicit-model')
  },
)
it('allows embedding policy edits without exposing identity fields', async () => {
  mount()
  await screen.findByText('model-a')
  fireEvent.click(
    section('embeddingType').getByRole('button', { name: 'models.parameters' }),
  )
  expect(
    screen.getByLabelText<HTMLInputElement>('models.fields.model').readOnly,
  ).toBe(true)
  expect(
    screen.getByLabelText<HTMLInputElement>('models.fields.dimension').readOnly,
  ).toBe(true)
  fireEvent.change(screen.getByLabelText('models.fields.max_retries'), {
    target: { value: '5' },
  })
  await apply()
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
  await waitFor(() =>
    expect(JSON.parse(state.save.mock.calls[0][0])).toEqual({
      ...file,
      embedding: {
        ...data.models.embedding.config,
        max_retries: 5,
      },
    }),
  )
})
it('retains the draft after backend validation failure', async () => {
  mount()
  state.save.mockRejectedValue(new Error('Validation failed'))
  await screen.findByText('model-a')
  await menuAction(section('vlmType'), 0, 'models.moveDown')
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
  await screen.findByRole('alert')
  expect(section('vlmType').getByText('models.unsaved')).toBeTruthy()
  expect(screen.getByRole('alert').textContent).toContain('Validation failed')
})

it('blocks editing through View while a save is pending and closes the stale view on success', async () => {
  mount()
  let finishSave!: (value: object) => void
  state.save.mockImplementation(
    () =>
      new Promise((resolve) => {
        finishSave = resolve
      }),
  )
  await screen.findByText('model-a')
  await menuAction(section('vlmType'), 0, 'models.moveDown')
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
  await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1))
  fireEvent.click(
    section('vlmType').getAllByRole('button', { name: 'models.view' })[0],
  )
  const dialog = within(screen.getByRole('dialog'))
  const edit = dialog.getByRole('button', { name: 'models.edit' })
  expect(edit.hasAttribute('disabled')).toBe(true)
  fireEvent.click(edit)
  expect(
    dialog.getByLabelText<HTMLInputElement>('models.fields.api_key').readOnly,
  ).toBe(true)
  expect(state.save).toHaveBeenCalledTimes(1)
  finishSave({})
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  expect(screen.queryByRole('button', { name: 'models.saveAll' })).toBeNull()
})
it('retains server diagnostics alongside the localized load failure', async () => {
  mount()
  state.get.mockRejectedValue(new Error('Startup file unavailable'))
  await screen.findByText('model-a')
  // Trigger a public refresh to fail after the initial successful read.
  fireEvent.click(screen.getByRole('button', { name: 'models.reloadFile' }))
  expect(await screen.findByText('Startup file unavailable')).toBeTruthy()
  expect(screen.getByText('models.loadFailed')).toBeTruthy()
})
it('retains unfinished dialog JSON through a failed background refresh', async () => {
  const client = mount()
  await screen.findByText('model-a')
  fireEvent.click(
    section('embeddingType').getByRole('button', { name: 'models.parameters' }),
  )
  const input = screen.getByLabelText<HTMLTextAreaElement>(
    'models.fields.circuit_breaker',
  )
  fireEvent.change(input, { target: { value: '{unfinished' } })
  state.get.mockRejectedValue(new Error('temporary read failure'))
  await act(async () => {
    await client.refetchQueries({
      queryKey: ['server-configuration', 'default'],
    })
  })
  expect(screen.getByRole('dialog')).toBeTruthy()
  expect(
    screen.getByLabelText<HTMLTextAreaElement>('models.fields.circuit_breaker')
      .value,
  ).toBe('{unfinished')
  state.get.mockResolvedValue({ ...structuredClone(data), content })
  await act(async () => {
    await client.refetchQueries({
      queryKey: ['server-configuration', 'default'],
    })
  })
  expect(
    screen.getByLabelText<HTMLTextAreaElement>('models.fields.circuit_breaker')
      .value,
  ).toBe('{unfinished')
})
it('uses localized validation text for invalid JSON', async () => {
  mount()
  await screen.findByText('model-a')
  fireEvent.click(
    section('embeddingType').getByRole('button', { name: 'models.edit' }),
  )
  const headers = screen.getByLabelText<HTMLTextAreaElement>(
    'models.fields.extra_headers',
  )
  fireEvent.change(headers, { target: { value: '{invalid' } })
  expect(headers.validationMessage).toBe('models.invalidJsonObject')
  expect(headers.checkValidity()).toBe(false)
  fireEvent.change(headers, { target: { value: '{"A":"B"}' } })
  expect(headers.validationMessage).toBe('')
})

it('shares form edits and whole-file edits across modes, preserving all other sections', async () => {
  mount()
  await screen.findByText('model-a')
  const tabs = within(screen.getByRole('tablist', { name: 'models.editMode' }))
  expect(
    tabs
      .getByRole('tab', { name: 'models.formMode' })
      .getAttribute('aria-selected'),
  ).toBe('true')
  fireEvent.click(
    section('vlmType').getAllByRole('button', { name: 'models.edit' })[0],
  )
  fireEvent.change(screen.getByLabelText('models.fields.model'), {
    target: { value: 'form-draft' },
  })
  await apply()
  fireEvent.click(screen.getByRole('tab', { name: 'models.fileMode' }))
  expect(
    tabs
      .getByRole('tab', { name: 'models.fileMode' })
      .getAttribute('aria-selected'),
  ).toBe('true')
  const input =
    await screen.findByLabelText<HTMLTextAreaElement>('models.fileContent')
  const raw = JSON.parse(input.value)
  expect(raw.vlm.credentials[0].model).toBe('form-draft')
  expect(raw.storage).toEqual(file.storage)
  expect(raw.server.root_api_key).toBe('${ROOT_KEY}')
  raw.server.port = 1934
  raw.vlm.credentials[0].model = 'file-draft'
  const text = JSON.stringify(raw, null, 4)
  fireEvent.change(input, { target: { value: text } })
  await act(async () => {
    fireEvent.click(screen.getByRole('tab', { name: 'models.formMode' }))
  })
  expect(await screen.findByText('file-draft')).toBeTruthy()
  expect(
    tabs
      .getByRole('tab', { name: 'models.formMode' })
      .getAttribute('aria-selected'),
  ).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(text, 'revision'))
  expect(state.save).toHaveBeenCalledTimes(1)
  expect(await screen.findByText('models.restartRequired')).toBeTruthy()
})
it('blocks invalid JSON from saving or switching, then discards the same draft', async () => {
  mount()
  await screen.findByText('model-a')
  fireEvent.click(screen.getByRole('tab', { name: 'models.fileMode' }))
  const input =
    await screen.findByLabelText<HTMLTextAreaElement>('models.fileContent')
  fireEvent.change(input, { target: { value: '{broken' } })
  expect(screen.getByText('models.invalidJsonObject')).toBeTruthy()
  expect(
    screen
      .getByRole('tab', { name: 'models.formMode' })
      .getAttribute('aria-disabled'),
  ).toBe('true')
  expect(
    screen
      .getByRole('button', { name: 'models.saveAll' })
      .hasAttribute('disabled'),
  ).toBe(true)
  expect(state.save).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'models.discardAll' }))
  expect(input.value).toBe(content)
  expect(screen.queryByRole('button', { name: 'models.saveAll' })).toBeNull()
})
it('keeps full-file text and its original revision after validation and save failures', async () => {
  const client = mount()
  await screen.findByText('model-a')
  fireEvent.click(screen.getByRole('tab', { name: 'models.fileMode' }))
  const text = JSON.stringify({
    ...file,
    server: { ...file.server, port: 1934 },
  })
  fireEvent.change(screen.getByLabelText('models.fileContent'), {
    target: { value: text },
  })
  state.preview.mockRejectedValue(new Error('Invalid startup configuration'))
  await act(async () => {
    fireEvent.click(screen.getByRole('tab', { name: 'models.formMode' }))
  })
  await screen.findByText(/Invalid startup configuration/)
  expect(
    screen.getByLabelText<HTMLTextAreaElement>('models.fileContent').value,
  ).toBe(text)
  state.get.mockResolvedValue({
    ...data,
    content,
    revision: 'external-revision',
  })
  await act(async () => {
    await client.refetchQueries({
      queryKey: ['server-configuration', 'default'],
    })
  })
  state.save.mockRejectedValue(new Error('ov.conf changed'))
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(text, 'revision'))
  expect(
    screen.getByLabelText<HTMLTextAreaElement>('models.fileContent').value,
  ).toBe(text)
})
it('keeps the form dialog and input when draft validation fails', async () => {
  mount()
  await screen.findByText('model-a')
  state.preview.mockRejectedValue(new Error('Invalid configuration fields'))
  fireEvent.click(
    section('vlmType').getAllByRole('button', { name: 'models.edit' })[0],
  )
  fireEvent.change(screen.getByLabelText('models.fields.model'), {
    target: { value: 'rejected-model' },
  })
  await apply()
  const dialog = within(screen.getByRole('dialog'))
  expect(await dialog.findByText(/Invalid configuration fields/)).toBeTruthy()
  expect(
    dialog.getByLabelText<HTMLInputElement>('models.fields.model').value,
  ).toBe('rejected-model')
  expect(state.save).not.toHaveBeenCalled()
})

it.each(['vlmType', 'embeddingType'])(
  'blocks all dialog inputs during draft validation for %s',
  async (kind) => {
    mount()
    await screen.findByText('model-a')
    let finishPreview!: (value: unknown) => void
    const project = state.preview.getMockImplementation()!
    state.preview.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          finishPreview = resolve
        }),
    )
    fireEvent.click(
      section(kind).getAllByRole('button', {
        name: kind === 'vlmType' ? 'models.edit' : 'models.parameters',
      })[0],
    )
    const dialog = screen.getByRole('dialog')
    const field = within(dialog).getByLabelText<HTMLInputElement>(
      kind === 'vlmType'
        ? 'models.fields.model'
        : 'models.fields.max_concurrent',
    )
    fireEvent.change(field, {
      target: { value: kind === 'vlmType' ? 'submitted' : '20' },
    })
    await apply()
    await waitFor(() => expect(field.readOnly).toBe(true))
    for (const input of dialog.querySelectorAll<
      HTMLInputElement | HTMLTextAreaElement
    >('input:not([aria-hidden="true"]):not([type="hidden"]), textarea')) {
      expect(input.readOnly || input.disabled, input.outerHTML).toBe(true)
    }
    // Even synthetic change events must not overwrite the in-flight snapshot.
    fireEvent.change(field, {
      target: { value: kind === 'vlmType' ? 'lost' : '30' },
    })
    const [text, changes] = state.preview.mock.calls[0]
    await act(async () => {
      finishPreview(await project(text, changes))
    })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
    await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1))
    const saved = JSON.parse(state.save.mock.calls[0][0])
    if (kind === 'vlmType')
      expect(saved.vlm.credentials[0].model).toBe('submitted')
    else expect(saved.embedding.max_concurrent).toBe(20)
  },
)

it('allows unquoted environment values in file drafts without resolving them in the browser', async () => {
  mount()
  await screen.findByText('model-a')
  fireEvent.click(screen.getByRole('tab', { name: 'models.fileMode' }))
  const text = content.replace('"port": 1933', '"port": ${STUDIO_PORT}')
  fireEvent.change(screen.getByLabelText('models.fileContent'), {
    target: { value: text },
  })
  expect(screen.queryByText('models.invalidJsonObject')).toBeNull()
  expect(
    screen
      .getByRole('tab', { name: 'models.formMode' })
      .hasAttribute('disabled'),
  ).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(text, 'revision'))
})

it.each([false, true])(
  'toggles the shared VLM thinking policy from %s without changing bindings',
  async (initial) => {
    const payload = structuredClone(data)
    Object.assign(payload.models.vlm.config, { thinking: initial })
    mount(payload)
    await screen.findByText('model-a')
    fireEvent.click(
      section('vlmType').getByRole('button', { name: 'models.parameters' }),
    )
    const thinking = screen.getByRole('switch', {
      name: 'models.fields.thinking',
    })
    expect(thinking.getAttribute('aria-checked')).toBe(String(initial))
    fireEvent.click(thinking)
    await apply()
    fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
    await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1))
    const saved = JSON.parse(state.save.mock.calls[0][0])
    expect(saved.vlm.thinking).toBe(!initial)
    expect(saved.vlm.credentials).toEqual(language.credentials)
    expect(saved.embedding).toEqual(file.embedding)
  },
)
it('saves before requesting restart and waits for a new service instance', async () => {
  mount()
  await screen.findByText('model-a')
  await menuAction(section('vlmType'), 0, 'models.moveDown')
  let finishRestart!: (value: {
    supported: boolean
    instance_id: string
    restarting: boolean
  }) => void
  state.restart.mockImplementation(
    () =>
      new Promise((resolve) => {
        finishRestart = resolve
      }),
  )
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAndRestart' }))
  await waitFor(() =>
    expect(state.restart).toHaveBeenCalledWith('saved-revision'),
  )
  expect(state.save.mock.invocationCallOrder[0]).toBeLessThan(
    state.restart.mock.invocationCallOrder[0],
  )
  expect(screen.getByText('models.restarting')).toBeTruthy()
  expect(
    section('vlmType')
      .getByRole('button', { name: 'models.addModel' })
      .hasAttribute('disabled'),
  ).toBe(true)
  await act(async () =>
    finishRestart({ supported: true, instance_id: 'old', restarting: true }),
  )
  await waitFor(() =>
    expect(screen.queryByText('models.restarting')).toBeNull(),
  )
  expect(
    screen.queryByRole('button', { name: 'models.saveAndRestart' }),
  ).toBeNull()
})
it('reports a recovered instance as a rollback rather than applying the draft', async () => {
  mount()
  await screen.findByText('model-a')
  await menuAction(section('vlmType'), 0, 'models.moveDown')
  state.restart.mockImplementation(async () => {
    state.get.mockResolvedValue({
      ...structuredClone(data),
      restart: {
        supported: true,
        instance_id: 'recovered',
        restarting: false,
        rolled_back: true,
      },
    })
    return { supported: true, instance_id: 'old', restarting: true }
  })
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAndRestart' }))
  expect(await screen.findByText('models.restartRolledBack')).toBeTruthy()
  expect(screen.queryByText('models.restarting')).toBeNull()
  expect(
    screen.queryByRole('button', { name: 'models.restartService' }),
  ).toBeNull()
})
it('does not restart after a save failure and preserves the draft', async () => {
  mount()
  await screen.findByText('model-a')
  await menuAction(section('vlmType'), 0, 'models.moveDown')
  state.save.mockRejectedValue(new Error('revision conflict'))
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAndRestart' }))
  await screen.findByText(/revision conflict/)
  expect(state.restart).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'models.discardAll' })).toBeTruthy()
})
it.each([false, true])(
  'keeps saved state and retry revision after restart rejection (refresh fails: %s)',
  async (refreshFails) => {
    mount()
    await screen.findByText('model-a')
    await menuAction(section('vlmType'), 0, 'models.moveDown')
    const recovered = {
      ...structuredClone(data),
      content,
      revision: 'saved-revision',
    }
    if (refreshFails)
      state.get.mockRejectedValue(new Error('temporary read failure'))
    else state.get.mockResolvedValue(recovered)
    state.restart.mockRejectedValue(new Error('restart unavailable'))
    fireEvent.click(
      screen.getByRole('button', { name: 'models.saveAndRestart' }),
    )
    await screen.findByText(/models.restartFailed/)
    expect(screen.getByText(/models.restartFailed/).textContent).toContain(
      'restart unavailable',
    )
    expect(
      screen.queryByRole('button', { name: 'models.discardAll' }),
    ).toBeNull()
    expect(
      screen
        .getByRole('button', { name: 'models.restartService' })
        .hasAttribute('disabled'),
    ).toBe(false)
    expect(state.save).toHaveBeenCalledTimes(1)
    expect(screen.getAllByText('models.saved').length).toBeGreaterThan(0)
    state.get.mockResolvedValue(recovered)
    state.restart.mockResolvedValue({
      supported: true,
      instance_id: 'old',
      restarting: true,
    })
    fireEvent.click(
      screen.getByRole('button', { name: 'models.restartService' }),
    )
    await waitFor(() => expect(state.restart).toHaveBeenCalledTimes(2))
    expect(state.restart.mock.calls[1]).toEqual(['saved-revision'])
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'models.restartService' }),
      ).toBeNull(),
    )
    expect(state.save).toHaveBeenCalledTimes(1)
  },
)

it('keeps the entire environment-bound form read-only and offers verbatim file editing', async () => {
  const payload = { ...structuredClone(data), form_readonly: true }
  mount(payload)
  await screen.findAllByText('models.environmentObject')
  expect(section('vlmType').queryAllByRole('button')).toHaveLength(0)
  expect(section('embeddingType').queryAllByRole('button')).toHaveLength(0)
  fireEvent.click(screen.getByRole('tab', { name: 'models.fileMode' }))
  const text = content.replace('"port": 1933', '"port": ${STUDIO_PORT}')
  fireEvent.change(await screen.findByLabelText('models.fileContent'), {
    target: { value: text },
  })
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(text, 'revision'))
})

it('retains the configuration draft when switching settings tabs', async () => {
  await act(async () => {
    mount(data, true)
  })
  fireEvent.click(
    await screen.findByRole('tab', { name: 'tabs.configuration' }),
  )
  await screen.findByText('model-a')
  fireEvent.click(screen.getByRole('tab', { name: 'models.fileMode' }))
  const text = content.replace('default-model', 'unsaved-model')
  fireEvent.change(
    await screen.findByRole<HTMLTextAreaElement>('textbox', {
      name: 'models.fileContent',
    }),
    { target: { value: text } },
  )
  fireEvent.click(screen.getByRole('tab', { name: 'tabs.connection' }))
  await waitFor(() =>
    expect(
      screen.queryByRole('textbox', { name: 'models.fileContent' }),
    ).toBeNull(),
  )
  fireEvent.click(screen.getByRole('tab', { name: 'tabs.configuration' }))
  expect(
    screen.getByRole<HTMLTextAreaElement>('textbox', {
      name: 'models.fileContent',
    }).value,
  ).toBe(text)
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
  await waitFor(() => expect(state.save).toHaveBeenCalledWith(text, 'revision'))
})

it('edits uppercase VikingDB credentials with the correct auth controls', async () => {
  const payload = structuredClone(data)
  const credential: ModelConfig =
    payload.models.embedding.config.dense.credentials[0]
  Object.assign(credential, {
    provider: 'VIKINGDB',
    ak: 'dummy-ak',
    sk: 'dummy-sk',
    region: 'cn-beijing',
  })
  delete credential.api_key
  mount(payload)
  await screen.findByText('model-a')
  fireEvent.click(
    section('embeddingType').getByRole('button', { name: 'models.edit' }),
  )
  const dialog = within(screen.getByRole('dialog'))
  expect(
    dialog.getByRole('combobox', { name: 'models.fields.provider' })
      .textContent,
  ).toContain('vikingdb')
  expect(
    dialog.getByLabelText<HTMLInputElement>('models.fields.ak').value,
  ).toBe('dummy-ak')
  expect(
    dialog.getByLabelText<HTMLInputElement>('models.fields.region').value,
  ).toBe('cn-beijing')
  expect(dialog.queryByLabelText('models.fields.api_key')).toBeNull()
  fireEvent.change(dialog.getByLabelText('models.fields.sk'), {
    target: { value: 'rotated-sk' },
  })
  await apply()
  expect(
    state.preview.mock.lastCall![1].embedding.dense.credentials[0],
  ).toMatchObject({ provider: 'VIKINGDB', sk: 'rotated-sk' })
})
