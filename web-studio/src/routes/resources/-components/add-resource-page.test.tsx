// @vitest-environment jsdom

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AddResourceForm } from './add-resource-page'

const uploadMocks = vi.hoisted(() => ({
  enqueueUploads: vi.fn(),
  resetRemote: vi.fn(),
  startRemote: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { resolvedLanguage: 'zh-CN' },
    t: (key: string) => key,
  }),
}))

vi.mock('../-hooks/use-resource-upload', () => ({
  useResourceUpload: () => ({
    ...uploadMocks,
    remoteState: {
      error: null,
      phase: 'idle',
      remoteUrl: '',
      skippedFiles: [],
      taskId: null,
    },
  }),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('AddResourceForm watch options', () => {
  it('shows supported resource types and switches their parameter forms', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" />
      </QueryClientProvider>,
    )

    expect(screen.getByText('sourcePicker.title')).toBeTruthy()
    const remoteUrlInput = screen.getByRole('textbox', { name: 'remoteUrl' })
    expect(remoteUrlInput.getAttribute('placeholder')).toBe(
      'remoteUrl.placeholder',
    )
    for (const type of ['feishu', 'git', 'webPage', 'remoteFile']) {
      expect(
        screen.getByRole('button', {
          name: new RegExp(`sourcePicker.${type}`),
        }),
      ).toBeTruthy()
    }

    fireEvent.click(screen.getByRole('button', { name: /sourcePicker.feishu/ }))
    expect(remoteUrlInput.getAttribute('placeholder')).toBe(
      'sourcePicker.feishuExample',
    )
    expect(screen.getByText('sourcePicker.feishuHint')).toBeTruthy()
    expect(screen.getByText('feishu.auth.title')).toBeTruthy()
    fireEvent.click(screen.getByText('configurationGuide.title'))
    expect(screen.getByText('feishu.configuration.server')).toBeTruthy()
    expect(
      screen
        .getByRole('link', { name: /configurationGuide.documentation/ })
        .getAttribute('href'),
    ).toBe('https://docs.openviking.ai/zh/guides/01-configuration#feishu')

    fireEvent.click(screen.getByRole('button', { name: /sourcePicker.feishu/ }))
    expect(screen.queryByText('sourcePicker.feishuHint')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /sourcePicker.feishu/ }))

    fireEvent.click(screen.getByRole('button', { name: /sourcePicker.git/ }))
    expect(remoteUrlInput.getAttribute('placeholder')).toBe(
      'sourcePicker.gitExample',
    )
    expect(screen.getByText('git.refType')).toBeTruthy()

    fireEvent.click(
      screen.getByRole('button', { name: /sourcePicker.webPage/ }),
    )
    expect(remoteUrlInput.getAttribute('placeholder')).toBe(
      'sourcePicker.webPageExample',
    )
    expect(screen.getByText('web.mode.title')).toBeTruthy()

    expect(
      screen.queryByRole('button', { name: /sourcePicker.tos/ }),
    ).toBeNull()
    expect(
      screen.queryByRole('button', { name: /sourcePicker.connector/ }),
    ).toBeNull()
  })

  it('lets the server validate an address that differs from the selected type', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" />
      </QueryClientProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: /sourcePicker.feishu/ }))
    fireEvent.change(screen.getByRole('textbox', { name: 'remoteUrl' }), {
      target: { value: 'https://github.com/volcengine/OpenViking' },
    })

    const submit = screen.getByRole('button', { name: 'startProcessing' })
    expect(submit.hasAttribute('disabled')).toBe(false)
    fireEvent.click(submit)
    expect(uploadMocks.startRemote).toHaveBeenCalledWith(
      expect.objectContaining({
        url: 'https://github.com/volcengine/OpenViking',
      }),
    )
  })

  it('hides unfinished TOS imports in the watch creation flow', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm
          initialMode="remote"
          initialWatchEnabled
          watchRequired
        />
      </QueryClientProvider>,
    )

    expect(screen.queryByRole('button', { name: 'mode.upload' })).toBeNull()
    expect(
      screen.queryByRole('button', { name: /sourcePicker.tos/ }),
    ).toBeNull()
    expect(screen.queryByText('tosOptions.title')).toBeNull()
    expect(
      screen
        .getByRole('button', { name: 'startProcessing' })
        .hasAttribute('disabled'),
    ).toBe(true)
  })

  it('clears source values when switching resource types', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" />
      </QueryClientProvider>,
    )

    fireEvent.click(screen.getByRole('button', { name: /sourcePicker.git/ }))
    const remoteUrlInput = screen.getByRole('textbox', { name: 'remoteUrl' })
    fireEvent.change(remoteUrlInput, {
      target: { value: 'https://github.com/volcengine/OpenViking' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: 'git.branch' }), {
      target: { value: 'feature/docs' },
    })

    fireEvent.click(screen.getByRole('button', { name: /sourcePicker.feishu/ }))
    expect((remoteUrlInput as HTMLInputElement).value).toBe('')

    fireEvent.click(screen.getByRole('button', { name: /sourcePicker.git/ }))
    expect(screen.getByRole('textbox', { name: 'git.branch' }).value).toBe('')
    expect(
      screen
        .getByRole('button', { name: 'git.branch' })
        .getAttribute('aria-pressed'),
    ).toBe('true')
  })

  it('submits watch_interval for a watched remote resource', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" initialWatchEnabled />
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'remoteUrl' }), {
      target: { value: 'https://github.com/volcengine/OpenViking' },
    })
    fireEvent.change(
      screen.getByRole('spinbutton', { name: 'watch.interval' }),
      { target: { value: '60' } },
    )
    fireEvent.click(screen.getByRole('button', { name: 'startProcessing' }))

    expect(uploadMocks.startRemote).toHaveBeenCalledWith({
      commonBody: expect.objectContaining({
        parent: 'viking://resources/',
        watch_interval: 60,
      }),
      onAccepted: undefined,
      onCompleted: undefined,
      onFailed: undefined,
      url: 'https://github.com/volcengine/OpenViking',
    })
  })

  it('submits Feishu user credentials and defers token validation', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" initialWatchEnabled />
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'remoteUrl' }), {
      target: { value: 'https://example.feishu.cn/docx/doc-token' },
    })
    fireEvent.click(screen.getByRole('radio', { name: /feishu.auth.user/ }))
    fireEvent.change(screen.getByLabelText('feishu.accessToken'), {
      target: { value: 'u-token' },
    })

    const submit = screen.getByRole('button', { name: 'startProcessing' })
    expect(submit.hasAttribute('disabled')).toBe(false)
    fireEvent.click(submit)

    expect(uploadMocks.startRemote).toHaveBeenCalledWith({
      commonBody: expect.objectContaining({
        args: {
          feishu_access_token: 'u-token',
          feishu_refresh_token: '',
        },
        watch_interval: 1440,
      }),
      onAccepted: undefined,
      onCompleted: undefined,
      onFailed: undefined,
      url: 'https://example.feishu.cn/docx/doc-token',
    })
  })

  it('submits Git commit and HTTPS credentials', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" />
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'remoteUrl' }), {
      target: { value: 'https://github.com/volcengine/OpenViking' },
    })
    const branchButton = screen.getByRole('button', { name: 'git.branch' })
    const commitButton = screen.getByRole('button', { name: 'git.commit' })
    expect(branchButton.getAttribute('aria-pressed')).toBe('true')
    fireEvent.click(commitButton)
    expect(commitButton.getAttribute('aria-pressed')).toBe('true')
    fireEvent.change(screen.getByRole('textbox', { name: 'git.commit' }), {
      target: { value: 'abc123' },
    })
    fireEvent.click(screen.getByRole('radio', { name: /git.auth.token/ }))
    fireEvent.change(screen.getByLabelText('git.username'), {
      target: { value: 'oauth-user' },
    })
    fireEvent.change(screen.getByLabelText('git.token'), {
      target: { value: 'secret-token' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'startProcessing' }))

    expect(uploadMocks.startRemote).toHaveBeenCalledWith({
      commonBody: expect.objectContaining({
        args: {
          auth_config: {
            token: 'secret-token',
            username: 'oauth-user',
          },
          commit: 'abc123',
        },
      }),
      onAccepted: undefined,
      onCompleted: undefined,
      onFailed: undefined,
      url: 'https://github.com/volcengine/OpenViking',
    })
  })

  it('clears HTTPS Git credentials when the source changes to SSH', async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" />
      </QueryClientProvider>,
    )

    const remoteUrlInput = screen.getByRole('textbox', { name: 'remoteUrl' })
    fireEvent.change(remoteUrlInput, {
      target: { value: 'https://github.com/volcengine/OpenViking' },
    })
    fireEvent.click(screen.getByRole('radio', { name: /git.auth.token/ }))
    fireEvent.change(screen.getByLabelText('git.token'), {
      target: { value: 'secret-token' },
    })

    fireEvent.change(remoteUrlInput, {
      target: { value: 'git@github.com:volcengine/OpenViking.git' },
    })

    await waitFor(() =>
      expect(
        screen
          .getByRole('radio', { name: /git.auth.public/ })
          .getAttribute('aria-checked'),
      ).toBe('true'),
    )
    fireEvent.click(screen.getByRole('button', { name: 'startProcessing' }))

    expect(uploadMocks.startRemote).toHaveBeenCalledWith({
      commonBody: expect.not.objectContaining({
        args: expect.objectContaining({ auth_config: expect.anything() }),
      }),
      onAccepted: undefined,
      onCompleted: undefined,
      onFailed: undefined,
      url: 'git@github.com:volcengine/OpenViking.git',
    })
  })

  it('restores native upload capabilities after detecting TOS', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" />
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'remoteUrl' }), {
      target: { value: 'tos://bucket/docs' },
    })
    expect(screen.getByText('destination.tosHint')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'mode.upload' }))

    expect(screen.getByText('destination.parentHint')).toBeTruthy()
    expect(screen.queryByText('destination.tosHint')).toBeNull()
    fireEvent.click(screen.getByText('advancedOptions'))
    expect(
      screen.getByRole('checkbox', { name: 'strict' }).hasAttribute('disabled'),
    ).toBe(false)
  })

  it('submits recursive web crawl options', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" />
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'remoteUrl' }), {
      target: { value: 'https://example.com/docs' },
    })
    fireEvent.click(screen.getByRole('radio', { name: /web.mode.recursive/ }))
    fireEvent.change(screen.getByLabelText('web.depth'), {
      target: { value: '2' },
    })
    fireEvent.change(screen.getByLabelText('web.maxPages'), {
      target: { value: '25' },
    })
    fireEvent.change(screen.getByLabelText('web.includePaths'), {
      target: { value: '/docs, /zh/' },
    })
    fireEvent.change(screen.getByLabelText('web.excludePaths'), {
      target: { value: '/archive' },
    })
    fireEvent.click(
      screen.getByRole('checkbox', { name: 'web.allowExternalLinks' }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'startProcessing' }))

    expect(uploadMocks.startRemote).toHaveBeenCalledWith({
      commonBody: expect.objectContaining({
        args: {
          allow_external_links: true,
          depth: 2,
          exclude_paths: ['/archive'],
          include_paths: ['/docs', '/zh/'],
          max_pages: 25,
          site: false,
          skip_download_links: true,
        },
      }),
      onAccepted: undefined,
      onCompleted: undefined,
      onFailed: undefined,
      url: 'https://example.com/docs',
    })
  })

  it('forwards server-owned option rules instead of blocking submission', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" />
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'remoteUrl' }), {
      target: { value: 'https://example.com/docs' },
    })
    fireEvent.click(screen.getByRole('radio', { name: /web.mode.recursive/ }))
    fireEvent.change(screen.getByLabelText('web.depth'), {
      target: { value: '-1' },
    })
    fireEvent.click(screen.getByText('advancedOptions'))
    fireEvent.change(screen.getByLabelText('tags'), {
      target: { value: 'server decides this format' },
    })
    fireEvent.click(screen.getByRole('checkbox', { name: 'waitForProcessing' }))
    fireEvent.change(screen.getByLabelText('timeout'), {
      target: { value: '-1' },
    })

    const submit = screen.getByRole('button', { name: 'startProcessing' })
    expect(submit.hasAttribute('disabled')).toBe(false)
    fireEvent.click(submit)

    expect(uploadMocks.startRemote).toHaveBeenCalledWith({
      commonBody: expect.objectContaining({
        args: expect.objectContaining({ depth: -1 }),
        tags: ['server decides this format'],
        timeout: -1,
      }),
      onAccepted: undefined,
      onCompleted: undefined,
      onFailed: undefined,
      url: 'https://example.com/docs',
    })
  })

  it('submits only server-supported site and feed options', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" />
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'remoteUrl' }), {
      target: { value: 'https://example.com/sitemap.xml' },
    })
    fireEvent.click(screen.getByRole('radio', { name: /web.mode.site/ }))
    fireEvent.change(screen.getByLabelText('web.maxPages'), {
      target: { value: '10' },
    })
    fireEvent.change(screen.getByLabelText('web.includePaths'), {
      target: { value: '/docs/*' },
    })
    fireEvent.change(screen.getByLabelText('web.excludePaths'), {
      target: { value: '/archive/*' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'startProcessing' }))

    expect(uploadMocks.startRemote).toHaveBeenCalledWith({
      commonBody: expect.objectContaining({
        args: {
          max_pages: 10,
          site: true,
        },
        exclude: '/archive/*',
        include: '/docs/*',
      }),
      onAccepted: undefined,
      onCompleted: undefined,
      onFailed: undefined,
      url: 'https://example.com/sitemap.xml',
    })
  })

  it('submits exact destination and common processing options', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" />
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'remoteUrl' }), {
      target: { value: 'https://example.com/guide.pdf' },
    })
    fireEvent.click(screen.getByRole('radio', { name: 'destination.exact' }))
    fireEvent.change(screen.getByLabelText('targetUri'), {
      target: { value: 'viking://resources/guides/guide.pdf' },
    })
    fireEvent.click(screen.getByText('advancedOptions'))
    fireEvent.change(screen.getByLabelText('tags'), {
      target: { value: 'team=docs, env=test' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'startProcessing' }))

    expect(uploadMocks.startRemote).toHaveBeenCalledWith({
      commonBody: expect.objectContaining({
        processing_mode: 'semantic_and_vectors',
        tag_mode: 'replace',
        tags: ['team=docs', 'env=test'],
        to: 'viking://resources/guides/guide.pdf',
        wait: false,
      }),
      onAccepted: undefined,
      onCompleted: undefined,
      onFailed: undefined,
      url: 'https://example.com/guide.pdf',
    })
    expect(
      uploadMocks.startRemote.mock.calls[0]?.[0].commonBody,
    ).not.toHaveProperty('parent')
    expect(
      uploadMocks.startRemote.mock.calls[0]?.[0].commonBody,
    ).not.toHaveProperty('preserve_structure')
  })

  it('submits TOS through the built-in server route', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={queryClient}>
        <AddResourceForm initialMode="remote" initialWatchEnabled />
      </QueryClientProvider>,
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'remoteUrl' }), {
      target: { value: 'tos://bucket/docs' },
    })
    fireEvent.change(screen.getByLabelText('targetUri'), {
      target: { value: 'viking://resources/custom' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'startProcessing' }))

    expect(uploadMocks.startRemote).toHaveBeenCalledWith({
      commonBody: expect.objectContaining({
        add_type: 'tos',
        directly_upload_media: true,
        processing_mode: 'semantic_and_vectors',
        strict: false,
        to: 'viking://resources/custom',
        wait: false,
      }),
      onAccepted: undefined,
      onCompleted: undefined,
      onFailed: undefined,
      url: 'tos://bucket/docs',
    })
    const commonBody = uploadMocks.startRemote.mock.calls[0]?.[0].commonBody
    expect(commonBody).not.toHaveProperty('parent')
    expect(commonBody).not.toHaveProperty('watch_interval')
    expect(commonBody).not.toHaveProperty('args')
    expect(commonBody).not.toHaveProperty('preserve_structure')
  })
})
