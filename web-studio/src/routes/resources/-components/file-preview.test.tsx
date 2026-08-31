// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { VikingFsEntry } from '../-types/viking-fm'
import {
  FilePreview,
  getJsonlMessage,
  normalizeJsonlDisplayText,
  parseJsonlRecords,
} from './file-preview'

const previewState = vi.hoisted(() => ({
  override: null as { content: string; fileType: string } | null,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('#/gen/ov-client/client.gen', () => ({
  client: {
    buildUrl: ({ query }: { query: { uri: string } }) =>
      `/api/v1/content/download?uri=${encodeURIComponent(query.uri)}`,
  },
}))

vi.mock('#/lib/ov-client', () => ({
  getContentDownload: vi.fn(),
  ovClient: { getOptions: () => ({ baseUrl: '' }) },
}))

vi.mock('../-hooks/viking-fm', () => ({
  useInvalidateVikingFs: () => ({
    invalidateList: vi.fn(),
    invalidatePreview: vi.fn(),
    invalidateTree: vi.fn(),
  }),
  useVikingFilePreview: () => ({
    canLoadContent: false,
    isContentLoaded: true,
    isFetching: false,
    isLoading: false,
    preview: {
      content: '[Target](./target.md)',
      fileType: 'markdown',
      shouldAutoRead: true,
      ...(previewState.override ?? {}),
    },
    refetch: vi.fn(),
  }),
  useVikingFsStat: () => ({
    data: undefined,
    isLoading: false,
  }),
}))

const file: VikingFsEntry = {
  abstract: '',
  isDir: false,
  modTime: '2026-08-04 12:00',
  modTimestamp: null,
  name: 'index.md',
  overview: '',
  size: '24 B',
  sizeBytes: 24,
  uri: 'viking://resources/wiki/index.md',
}

const directory: VikingFsEntry = {
  ...file,
  isDir: true,
  name: 'wiki',
  overview: '',
  uri: 'viking://resources/wiki',
}

function renderPreview(
  entry: VikingFsEntry,
  onNavigate: (uri: string) => void,
  directoryOverview?: string,
) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  if (entry.isDir) {
    queryClient.setQueryData(
      ['viking-directory-level', entry.uri, 'abstract'],
      '',
    )
    queryClient.setQueryData(
      ['viking-directory-level', entry.uri, 'overview'],
      directoryOverview,
    )
  }
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )

  return render(
    <FilePreview
      file={entry}
      onClose={vi.fn()}
      onNavigate={onNavigate}
      showCloseButton={false}
    />,
    { wrapper },
  )
}

describe('FilePreview Markdown links', () => {
  it('opens internal Markdown links in the resource preview', () => {
    const onNavigate = vi.fn()
    renderPreview(file, onNavigate)

    const link = screen.getByRole('link', { name: 'Target' })
    expect(link.getAttribute('href')).toBe('viking://resources/wiki/target.md')

    fireEvent.click(link)

    expect(onNavigate).toHaveBeenCalledOnce()
    expect(onNavigate).toHaveBeenCalledWith('viking://resources/wiki/target.md')
  })

  it('opens viking links from a directory overview', () => {
    const onNavigate = vi.fn()
    renderPreview(
      directory,
      onNavigate,
      [
        '[Target file](viking://resources/wiki/target.md)',
        '[Target directory](viking://resources/wiki/target-directory)',
      ].join('\n\n'),
    )

    const fileLink = screen.getByRole('link', { name: 'Target file' })
    const directoryLink = screen.getByRole('link', {
      name: 'Target directory',
    })
    expect(fileLink.getAttribute('href')).toBe(
      'viking://resources/wiki/target.md',
    )
    expect(directoryLink.getAttribute('href')).toBe(
      'viking://resources/wiki/target-directory',
    )

    fireEvent.click(fileLink)
    fireEvent.click(directoryLink)

    expect(onNavigate).toHaveBeenNthCalledWith(
      1,
      'viking://resources/wiki/target.md',
    )
    expect(onNavigate).toHaveBeenNthCalledWith(
      2,
      'viking://resources/wiki/target-directory',
    )
  })

  it('decodes encoded viking links before navigating', () => {
    const onNavigate = vi.fn()
    renderPreview(
      directory,
      onNavigate,
      '[目标](viking://resources/%E8%B5%84%E6%96%99/%E7%9B%AE%E6%A0%87.md)',
    )

    fireEvent.click(screen.getByRole('link', { name: '目标' }))

    expect(onNavigate).toHaveBeenCalledWith('viking://resources/资料/目标.md')
  })

  it('preserves non-viking links in a directory overview', () => {
    const onNavigate = vi.fn()
    renderPreview(
      directory,
      onNavigate,
      [
        '[Relative](child.md)',
        '[Protocol relative](//example.com/child.md)',
        '[External](https://example.com/child.md)',
        '[Data](data:text/html,unsafe)',
        '[Blob](blob:https://example.com/id)',
      ].join('\n\n'),
    )

    expect(
      screen.getByRole('link', { name: 'Relative' }).getAttribute('href'),
    ).toBe('child.md')
    expect(
      screen
        .getByRole('link', { name: 'Protocol relative' })
        .getAttribute('href'),
    ).toBe('//example.com/child.md')
    const externalLink = screen.getByRole('link', { name: 'External' })
    expect(externalLink.getAttribute('href')).toBe(
      'https://example.com/child.md',
    )
    expect(externalLink.getAttribute('target')).toBeNull()
    expect(screen.queryByRole('link', { name: 'Data' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'Blob' })).toBeNull()
    expect(onNavigate).not.toHaveBeenCalled()
  })
})

describe('normalizeJsonlDisplayText', () => {
  it('restores encoded and carriage-return line breaks', () => {
    expect(normalizeJsonlDisplayText('a↵b')).toBe('a\nb')
    expect(normalizeJsonlDisplayText('a⏎b')).toBe('a\nb')
    expect(normalizeJsonlDisplayText('a\r\nb')).toBe('a\nb')
    expect(normalizeJsonlDisplayText('a\rb')).toBe('a\nb')
  })

  it('leaves plain text untouched', () => {
    expect(normalizeJsonlDisplayText('a\nb')).toBe('a\nb')
    expect(normalizeJsonlDisplayText('plain text')).toBe('plain text')
  })
})

function classify(...lines: Array<string>) {
  return parseJsonlRecords(lines.join('\n')).map(getJsonlMessage)
}

describe('JSONL message classification', () => {
  it('classifies a plain-text user message from the OpenViking parts shape', () => {
    const [message] = classify(
      JSON.stringify({
        created_at: '2026-08-04T12:00:00Z',
        id: 'm1',
        parts: [{ text: 'hello there', type: 'text' }],
        role: 'user',
      }),
    )

    expect(message.role).toBe('user')
    expect(message.label).toBe('user')
    expect(message.toolShape).toBe('none')
    expect(message.parts).toEqual([{ kind: 'text', text: 'hello there' }])
  })

  it('classifies a plain-text assistant message', () => {
    const [message] = classify(
      JSON.stringify({
        id: 'm2',
        parts: [{ text: 'sure thing', type: 'text' }],
        role: 'assistant',
      }),
    )

    expect(message.role).toBe('assistant')
    expect(message.toolShape).toBe('none')
    expect(message.parts).toEqual([{ kind: 'text', text: 'sure thing' }])
  })

  it('keeps text and tool_use as separate parts on one assistant message', () => {
    const [message] = classify(
      JSON.stringify({
        message: {
          content: [
            { text: 'Let me read that file.', type: 'text' },
            {
              id: 'call_1',
              input: { file_path: '/tmp/a.txt' },
              name: 'Read',
              type: 'tool_use',
            },
          ],
          role: 'assistant',
        },
        type: 'assistant',
        uuid: 'u1',
      }),
    )

    expect(message.role).toBe('assistant')
    // A message carrying its own text is styled by role, not as a tool card.
    expect(message.toolShape).toBe('none')
    expect(message.parts).toEqual([
      { kind: 'text', text: 'Let me read that file.' },
      {
        input: { file_path: '/tmp/a.txt' },
        kind: 'tool-call',
        toolName: 'Read',
      },
    ])
    const textParts = message.parts.filter((part) => part.kind === 'text')
    expect(textParts).toHaveLength(1)
    for (const part of textParts) {
      expect(part.text).not.toContain('[tool:')
    }
  })

  it('classifies a tool_use-only assistant message as a tool call', () => {
    const [message] = classify(
      JSON.stringify({
        message: {
          content: [{ id: 'c', input: {}, name: 'Bash', type: 'tool_use' }],
          role: 'assistant',
        },
        type: 'assistant',
      }),
    )

    expect(message.role).toBe('assistant')
    expect(message.toolShape).toBe('call')
    expect(message.label).toBe('assistant')
  })

  it('classifies a tool_result carried by a user message', () => {
    const [message] = classify(
      JSON.stringify({
        message: {
          content: [
            {
              content: 'file contents',
              tool_use_id: 'call_1',
              type: 'tool_result',
            },
          ],
          role: 'user',
        },
        type: 'user',
      }),
    )

    // tool-result wins over role=user so the dashed tool-result card is kept.
    expect(message.role).toBe('user')
    expect(message.toolShape).toBe('result')
    expect(message.label).toBe('tool-result')
    expect(message.parts).toEqual([
      { kind: 'tool-result', text: 'file contents', toolName: '' },
    ])
  })

  it('flattens a tool_result whose content is a list of text blocks', () => {
    const [message] = classify(
      JSON.stringify({
        message: {
          content: [
            {
              content: [
                { text: 'line one', type: 'text' },
                { text: 'line two', type: 'text' },
              ],
              type: 'tool_result',
            },
          ],
          role: 'user',
        },
      }),
    )

    expect(message.parts).toEqual([
      { kind: 'tool-result', text: 'line one\nline two', toolName: '' },
    ])
  })

  it('expands an OpenViking tool part into a call and its output', () => {
    const [message] = classify(
      JSON.stringify({
        parts: [
          {
            tool_input: { pattern: '*.md' },
            tool_name: 'glob',
            tool_output: 'a.md\nb.md',
            type: 'tool',
          },
        ],
        role: 'assistant',
      }),
    )

    expect(message.toolShape).toBe('call')
    expect(message.parts).toEqual([
      { input: { pattern: '*.md' }, kind: 'tool-call', toolName: 'glob' },
      { kind: 'tool-result', text: 'a.md\nb.md', toolName: 'glob' },
    ])
  })

  it('classifies an OpenViking tool part with only an output as a result', () => {
    // Shape written by the claude-code peer: the result lands as its own
    // role=user record with no `tool_input` at all.
    const [message] = classify(
      JSON.stringify({
        id: 'msg_dca71c5d',
        parts: [
          {
            tool_name: 'Bash',
            tool_output: '33 failed, 392 passed',
            tool_status: 'completed',
            type: 'tool',
          },
        ],
        role: 'user',
      }),
    )

    expect(message.parts).toEqual([
      { kind: 'tool-result', text: '33 failed, 392 passed', toolName: 'Bash' },
    ])
    expect(message.toolShape).toBe('result')
    expect(message.label).toBe('tool-result')
  })

  it('treats a null tool_input with an output as a result-only part', () => {
    const [message] = classify(
      JSON.stringify({
        parts: [
          {
            tool_input: null,
            tool_name: 'Bash',
            tool_output: 'ok',
            type: 'tool',
          },
        ],
        role: 'user',
      }),
    )

    expect(message.parts).toEqual([
      { kind: 'tool-result', text: 'ok', toolName: 'Bash' },
    ])
    expect(message.toolShape).toBe('result')
  })

  it('keeps a call part for a tool part that has no output yet', () => {
    const [message] = classify(
      JSON.stringify({
        parts: [{ tool_name: 'Bash', type: 'tool' }],
        role: 'assistant',
      }),
    )

    expect(message.toolShape).toBe('call')
    expect(message.parts).toEqual([
      { input: {}, kind: 'tool-call', toolName: 'Bash' },
    ])
  })

  it('reads the role and timestamp from a nested message field', () => {
    const [message] = classify(
      JSON.stringify({
        message: { content: 'nested body', role: 'assistant' },
        timestamp: '2026-08-04T12:00:00Z',
        uuid: 'u9',
      }),
    )

    expect(message.role).toBe('assistant')
    expect(message.id).toBe('u9')
    expect(message.time).toBe('2026-08-04T12:00:00Z')
    expect(message.parts).toEqual([{ kind: 'text', text: 'nested body' }])
  })

  it('treats a plain string content as a single text part', () => {
    const [message] = classify(
      JSON.stringify({ content: 'just a string', role: 'user' }),
    )

    expect(message.toolShape).toBe('none')
    expect(message.parts).toEqual([{ kind: 'text', text: 'just a string' }])
  })

  it('falls back to a raw part for an unknown record shape', () => {
    const [message] = classify(
      JSON.stringify({ event: 'ping', type: 'system' }),
    )

    expect(message.role).toBe('other')
    expect(message.parts).toHaveLength(1)
    expect(message.parts[0].kind).toBe('raw')
  })

  it('marks an unparsable line as invalid and keeps it verbatim', () => {
    const [message] = classify('{not json')

    expect(message.role).toBe('invalid')
    expect(message.label).toBe('invalid')
    expect(message.parts).toEqual([{ kind: 'raw', text: '{not json' }])
  })

  it('skips blank lines and numbers records by their source line', () => {
    const messages = classify(
      JSON.stringify({ content: 'one', role: 'user' }),
      '',
      JSON.stringify({ content: 'two', role: 'assistant' }),
    )

    expect(messages).toHaveLength(2)
    expect(messages.map((message) => message.lineNo)).toEqual([1, 3])
  })
})

describe('FilePreview JSONL rendering', () => {
  beforeEach(() => {
    // Auto-cleanup is off in this setup, so each render starts from a bare DOM.
    cleanup()
    // This environment ships a non-functional window.localStorage.
    vi.stubGlobal('localStorage', {
      getItem: () => null,
      setItem: () => undefined,
    })
  })

  afterEach(() => {
    previewState.override = null
    vi.unstubAllGlobals()
  })

  it('renders text and tool_use of one assistant message separately', () => {
    previewState.override = {
      content: JSON.stringify({
        message: {
          content: [
            { text: 'Let me read that file.', type: 'text' },
            {
              id: 'call_1',
              input: { file_path: '/tmp/a.txt' },
              name: 'Read',
              type: 'tool_use',
            },
          ],
          role: 'assistant',
        },
        type: 'assistant',
      }),
      fileType: 'jsonl',
    }

    const { container } = renderPreview(file, vi.fn())

    expect(screen.getByText('Let me read that file.')).toBeTruthy()
    // The tool name is a badge, and the input renders as JSON, not as a
    // "[tool: ...]" prefix smuggled into the message body.
    expect(screen.getByText('Read')).toBeTruthy()
    expect(container.textContent).toContain('"file_path"')
    expect(container.textContent).not.toContain('[tool:')
  })

  it('renders a result-only tool record without an empty input block', () => {
    previewState.override = {
      content: JSON.stringify({
        parts: [
          {
            tool_name: 'Bash',
            tool_output: '33 failed, 392 passed',
            tool_status: 'completed',
            type: 'tool',
          },
        ],
        role: 'user',
      }),
      fileType: 'jsonl',
    }

    const { container } = renderPreview(file, vi.fn())

    expect(container.textContent).not.toContain('{}')
    // The tool name badge survives on a card that carries only the result.
    expect(screen.getByText('Bash')).toBeTruthy()
    expect(screen.getByText('33 failed, 392 passed')).toBeTruthy()
    expect(screen.getByText('tool-result')).toBeTruthy()
  })

  it('shows the no-arguments placeholder for an empty tool_use input', () => {
    previewState.override = {
      content: JSON.stringify({
        message: {
          content: [{ id: 'c', input: {}, name: 'Bash', type: 'tool_use' }],
          role: 'assistant',
        },
        type: 'assistant',
      }),
      fileType: 'jsonl',
    }

    const { container } = renderPreview(file, vi.fn())

    expect(container.textContent).not.toContain('{}')
    expect(screen.getByText('filePreview.jsonl.noArguments')).toBeTruthy()
  })

  it('keeps the dialog-mode label and the tool toggle in both modes', () => {
    previewState.override = {
      content: JSON.stringify({
        parts: [
          { tool_name: 'Bash', tool_output: 'done', type: 'tool' },
          { text: 'hi', type: 'text' },
        ],
        role: 'assistant',
      }),
      fileType: 'jsonl',
    }

    const { container } = renderPreview(file, vi.fn())
    const modeToggle = () => {
      const boxes = container.querySelectorAll('input[type="checkbox"]')
      return boxes[boxes.length - 1]
    }

    expect(screen.getByText('filePreview.jsonl.dialogMode')).toBeTruthy()
    expect(screen.getByText('filePreview.jsonl.toolcall')).toBeTruthy()

    fireEvent.click(modeToggle())

    expect(screen.getByText('filePreview.jsonl.dialogMode')).toBeTruthy()
    expect(screen.queryByText('filePreview.jsonl.rawMode')).toBeNull()
    // The tool toggle stays reachable once the raw list is showing.
    expect(screen.getByText('filePreview.jsonl.toolcall')).toBeTruthy()
  })

  it('hides tool-only records in raw mode when tool calls are off', () => {
    vi.stubGlobal('localStorage', {
      getItem: () => 'false',
      setItem: () => undefined,
    })
    previewState.override = {
      content: [
        JSON.stringify({
          parts: [{ text: 'hello there', type: 'text' }],
          role: 'user',
        }),
        JSON.stringify({
          parts: [
            { tool_name: 'Bash', tool_output: 'tool output', type: 'tool' },
          ],
          role: 'user',
        }),
      ].join('\n'),
      fileType: 'jsonl',
    }

    const { container } = renderPreview(file, vi.fn())

    // Dialog mode: the tool-only message drops out entirely.
    expect(screen.queryByText('Bash')).toBeNull()
    expect(screen.getByText('hello there')).toBeTruthy()

    const boxes = container.querySelectorAll('input[type="checkbox"]')
    fireEvent.click(boxes[boxes.length - 1])

    // Raw mode: line 1 still listed, the tool-only line 2 is gone.
    expect(screen.getByText('1')).toBeTruthy()
    expect(screen.queryByText('2')).toBeNull()
  })
})
