// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import userEvent from '@testing-library/user-event'
import { ModelManagement } from './model-management'
import zh from '#/i18n/locales/zh-CN/workspace'
import en from '#/i18n/locales/en/workspace'

const state = vi.hoisted(() => ({
  role: 'root',
  get: vi.fn(),
  save: vi.fn(),
  copy: vi.fn(),
}))
vi.mock('#/lib/clipboard', () => ({ copyTextToClipboard: state.copy }))
vi.mock('../-lib/model-management-api', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  createModelManagementApi: () => ({ get: state.get, save: state.save }),
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: { accountId: 'default' },
    connectionRole: state.role,
    identityScopeKey: 'default',
    serverMode: 'api_key',
  }),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
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
const data = {
  file_path: '/server/ov.conf',
  writable: true,
  restart_required: false,
  revision: 'revision',
  settings: {},
  models: {
    vlm: { source: 'server', config: language },
    query_planner: { source: 'vlm', config: language },
    embedding: {
      source: 'server',
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
    rerank: {
      source: 'server',
      available: true,
      config: {
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
      },
    },
  },
}
function mount(
  payload: typeof data & {
    overrides?: { cluster: string[]; account: string[] }
  } = data,
) {
  state.get.mockResolvedValue(structuredClone(payload))
  state.save.mockResolvedValue({})
  state.copy.mockResolvedValue(undefined)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ModelManagement />
    </QueryClientProvider>,
  )
}
async function menuAction(
  region: ReturnType<typeof within>,
  index: number,
  name: string,
) {
  fireEvent.click(region.getAllByRole('button', { name: 'models.more' })[index])
  fireEvent.click(await screen.findByRole('menuitem', { name }))
}
function section(name: string) {
  return within(screen.getByRole('region', { name: `models.${name}` }))
}
it('includes localized contract labels in model settings', () => {
  expect(zh.settings.models.fields.dimension).toBe('向量维度')
  expect(en.settings.models.fields.dimension).toBe('Dimensions')
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
  fireEvent.click(screen.getByText('models.apply'))
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
  fireEvent.click(
    within(screen.getByRole('dialog')).getByRole('button', {
      name: 'models.remove',
    }),
  )
  expect(vlm.queryByText('new-model')).toBeNull()
  fireEvent.click(vlm.getAllByRole('button', { name: 'models.edit' })[0])
  fireEvent.change(screen.getByLabelText('models.fields.model'), {
    target: { value: 'discarded' },
  })
  fireEvent.click(screen.getByText('models.dismiss'))
  expect(vlm.queryByText('discarded')).toBeNull()
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
it('places each add button alongside model settings in the section header', async () => {
  mount()
  await screen.findByText('model-a')
  for (const kind of ['vlmType', 'embeddingType']) {
    const region = section(kind)
    const add = region.getByRole('button', { name: 'models.addModel' })
    const parameters = region.getByRole('button', { name: 'models.parameters' })
    expect(add.parentElement).toBe(parameters.parentElement)
  }
  fireEvent.click(
    section('embeddingType').getByRole('button', { name: 'models.addModel' }),
  )
  fireEvent.change(screen.getByLabelText('models.fields.api_key'), {
    target: { value: 'new-embedding-key' },
  })
  fireEvent.click(screen.getByText('models.apply'))
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
  fireEvent.click(screen.getByText('models.apply'))
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
    fireEvent.click(screen.getByText('models.apply'))
    expect(
      section(kind).getByText('https://gateway.example.com/v1'),
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
    await waitFor(() => expect(state.save).toHaveBeenCalledTimes(1))
    const changes = state.save.mock.calls[0][0]
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
  await menuAction(section('vlmType'), 0, 'models.moveDown')
  fireEvent.click(
    section('embeddingType').getByRole('button', { name: 'models.parameters' }),
  )
  fireEvent.change(screen.getByLabelText('models.fields.max_retries'), {
    target: { value: '5' },
  })
  fireEvent.click(screen.getByText('models.apply'))
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
  await waitFor(() =>
    expect(state.save).toHaveBeenCalledWith(
      {
        vlm: {
          ...language,
          credentials: [language.credentials[1], language.credentials[0]],
        },
        embedding: { ...data.models.embedding.config, max_retries: 5 },
      },
      'revision',
    ),
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
  fireEvent.click(screen.getByText('models.apply'))
  fireEvent.click(screen.getByRole('button', { name: 'models.saveAll' }))
  await waitFor(() =>
    expect(state.save).toHaveBeenCalledWith(
      {
        embedding: {
          ...data.models.embedding.config,
          max_retries: 5,
        },
      },
      'revision',
    ),
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
