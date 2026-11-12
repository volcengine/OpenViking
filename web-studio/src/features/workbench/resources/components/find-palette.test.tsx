// @vitest-environment jsdom

import i18n from '#/i18n'
import { FindPalette } from '#/features/workbench/resources/components/find-palette'
import { openSearchPanel } from '@codemirror/search'
import { EditorView } from '@codemirror/view'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => {
  const entries = [
    {
      uri: 'viking://resources/one.md',
      name: 'one.md',
      isDir: false,
      size: 4,
      abstract: '',
    },
    {
      uri: 'viking://resources/two.md',
      name: 'two.md',
      isDir: false,
      size: 4,
      abstract: '',
    },
  ]
  return { entries, listing: { entries }, save: vi.fn() }
})

vi.mock('#/features/workbench/resources/hooks/viking-fm', () => ({
  useVikingFsList: () => ({
    data: mocks.listing,
    isSuccess: true,
  }),
  useVikingFsTree: () => ({ data: { nodes: [] } }),
  useVikingFsStat: () => ({}),
}))
vi.mock('#/features/workbench/retrieval/hooks/use-retrieval-query', () => ({
  useRetrievalQuery: () => ({}),
}))
vi.mock(
  '#/features/workbench/resources/components/lazy-file-preview',
  async () => {
    const { CodeEditor } = await import('#/shared/components/code-editor')
    return {
      LazyFilePreview: ({ file }: { file: { uri: string; name: string } }) => (
        <div data-testid="preview" data-uri={file.uri}>
          <CodeEditor
            initialContent="draft"
            filename={file.name}
            enableLanguageSupport={false}
          />
          <button type="button" onClick={mocks.save}>
            Save draft
          </button>
        </div>
      ),
    }
  },
)

beforeEach(async () => {
  vi.clearAllMocks()
  await i18n.changeLanguage('en')
  Element.prototype.scrollIntoView = vi.fn()
  Object.defineProperty(Range.prototype, 'getClientRects', {
    configurable: true,
    value: () => [],
  })
  Object.defineProperty(Range.prototype, 'getBoundingClientRect', {
    configurable: true,
    value: () => new DOMRect(),
  })
})
afterEach(cleanup)

function setup() {
  const onClose = vi.fn()
  const onNavigate = vi.fn()
  const result = render(
    <FindPalette
      open
      onClose={onClose}
      onNavigate={onNavigate}
      onNavigateDir={vi.fn()}
      scopeUri="viking://resources/"
    />,
  )
  return { ...result, onClose, onNavigate }
}

it('keeps Enter in the preview editor instead of navigating away from the draft', async () => {
  const { container, onClose, onNavigate } = setup()
  await waitFor(() =>
    expect(container.querySelector('.cm-editor')).not.toBeNull(),
  )
  const view = EditorView.findFromDOM(
    container.querySelector<HTMLElement>('.cm-editor')!,
  )!
  fireEvent.keyDown(view.contentDOM, { key: 'Enter', code: 'Enter' })
  expect(view.state.doc.toString()).toBe('\ndraft')
  expect(onClose).not.toHaveBeenCalled()
  expect(onNavigate).not.toHaveBeenCalled()
})

it('keeps arrow navigation in the preview editor instead of changing the selected file', async () => {
  const { container } = setup()
  await waitFor(() =>
    expect(container.querySelector('.cm-editor')).not.toBeNull(),
  )
  const view = EditorView.findFromDOM(
    container.querySelector<HTMLElement>('.cm-editor')!,
  )!
  fireEvent.keyDown(view.contentDOM, { key: 'ArrowDown', code: 'ArrowDown' })
  expect(screen.getByTestId('preview').getAttribute('data-uri')).toBe(
    mocks.entries[0].uri,
  )
})

it('does not turn Enter on a preview action button into file navigation', async () => {
  const { onClose, onNavigate } = setup()
  const input = screen.getByPlaceholderText(
    i18n.t('resources:searchPalette.placeholder'),
  )
  await waitFor(() => expect(document.activeElement).toBe(input))
  const button = screen.getByRole('button', { name: 'Save draft' })
  button.focus()
  await userEvent.setup().keyboard('{Enter}')
  expect(mocks.save).toHaveBeenCalledOnce()
  expect(onClose).not.toHaveBeenCalled()
  expect(onNavigate).not.toHaveBeenCalled()
})

it.each(['', '/'])(
  'keeps palette navigation and Escape available for query %j',
  (query) => {
    const { onClose, onNavigate } = setup()
    const input = screen.getByPlaceholderText(
      i18n.t('resources:searchPalette.placeholder'),
    )
    fireEvent.change(input, { target: { value: query } })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(screen.getByTestId('preview').getAttribute('data-uri')).toBe(
      mocks.entries[1].uri,
    )
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onNavigate).toHaveBeenCalledWith(mocks.entries[1].uri)
    expect(onClose).toHaveBeenCalledOnce()
    fireEvent.keyDown(input, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(2)
  },
)

it('lets the editor handle Escape in its search panel before the palette', async () => {
  const { container, onClose } = setup()
  await waitFor(() =>
    expect(container.querySelector('.cm-editor')).not.toBeNull(),
  )
  const view = EditorView.findFromDOM(
    container.querySelector<HTMLElement>('.cm-editor')!,
  )!
  act(() => {
    openSearchPanel(view)
  })
  const input = screen.getByPlaceholderText('Find')
  fireEvent.keyDown(input, { key: 'Escape', code: 'Escape' })
  expect(onClose).not.toHaveBeenCalled()
  expect(screen.queryByPlaceholderText('Find')).toBeNull()
})

it('does not navigate while the search input is committing an IME candidate', () => {
  const { onClose, onNavigate } = setup()
  const input = screen.getByPlaceholderText(
    i18n.t('resources:searchPalette.placeholder'),
  )
  fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
  fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 })
  expect(onClose).not.toHaveBeenCalled()
  expect(onNavigate).not.toHaveBeenCalled()
})

it('keeps Tab indentation in the editor and mode cycling in the search input', async () => {
  const { container } = setup()
  await waitFor(() =>
    expect(container.querySelector('.cm-editor')).not.toBeNull(),
  )
  const view = EditorView.findFromDOM(
    container.querySelector<HTMLElement>('.cm-editor')!,
  )!
  fireEvent.keyDown(view.contentDOM, { key: 'Tab', code: 'Tab' })
  expect(view.state.doc.toString()).toBe('  draft')
  const input = screen.getByPlaceholderText(
    i18n.t('resources:searchPalette.placeholder'),
  )
  fireEvent.keyDown(input, { key: 'Tab' })
  expect(
    screen.getByPlaceholderText(i18n.t('retrieval:placeholders.find')),
  ).toBeTruthy()
})
