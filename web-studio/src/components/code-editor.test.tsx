// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it } from 'vitest'
import { EditorView } from '@codemirror/view'
import { gotoLine, openSearchPanel } from '@codemirror/search'
import { undoDepth } from '@codemirror/commands'
import i18n from '#/i18n'
import { useTranslation } from 'react-i18next'
import { CodeEditor } from './code-editor'

// jsdom has no layout engine; CodeMirror still schedules range measurements.
beforeEach(() => {
  Object.defineProperty(Range.prototype, 'getClientRects', {
    configurable: true,
    value: () => [],
  })
  Object.defineProperty(Range.prototype, 'getBoundingClientRect', {
    configurable: true,
    value: () => new DOMRect(),
  })
})

function LocalizedEditor() {
  const { t } = useTranslation('studio')
  return (
    <CodeEditor
      initialContent="alpha"
      filename="sample.txt"
      ariaLabel={t('codeEditor.find')}
      enableLanguageSupport={false}
    />
  )
}

afterEach(async () => {
  cleanup()
  await i18n.changeLanguage('en')
})

it('switches the search panel language without losing editor content, selection or undo history', async () => {
  await i18n.changeLanguage('en')
  const { container } = render(<LocalizedEditor />)
  await waitFor(() =>
    expect(container.querySelector('.cm-editor')).not.toBeNull(),
  )
  const view = EditorView.findFromDOM(
    container.querySelector<HTMLElement>('.cm-editor')!,
  )!
  act(() => {
    view.dispatch({
      changes: { from: 5, insert: ' beta' },
      selection: { anchor: 10 },
    })
    openSearchPanel(view)
  })
  expect(screen.getByPlaceholderText('Find')).toBeTruthy()
  const history = undoDepth(view.state)
  await act(async () => {
    await i18n.changeLanguage('zh-CN')
  })
  expect(screen.getByPlaceholderText('查找')).toBeTruthy()
  expect(
    EditorView.findFromDOM(container.querySelector<HTMLElement>('.cm-editor')!),
  ).toBe(view)
  expect(view.state.doc.toString()).toBe('alpha beta')
  expect(view.state.selection.main.anchor).toBe(10)
  expect(undoDepth(view.state)).toBe(history)
  await act(async () => {
    await i18n.changeLanguage('en')
  })
  expect(screen.getByPlaceholderText('Find')).toBeTruthy()
})

it('localizes an open goto dialog while preserving the draft, selection and navigation', async () => {
  await i18n.changeLanguage('en')
  const { container } = render(
    <CodeEditor
      initialContent={'one\ntwo\nthree'}
      filename="sample.txt"
      enableLanguageSupport={false}
    />,
  )
  await waitFor(() =>
    expect(container.querySelector('.cm-editor')).not.toBeNull(),
  )
  const view = EditorView.findFromDOM(
    container.querySelector<HTMLElement>('.cm-editor')!,
  )!
  act(() => {
    gotoLine(view)
  })
  const input = screen.getByRole<HTMLInputElement>('textbox', {
    name: 'Go to line:',
  })
  fireEvent.change(input, { target: { value: '3' } })
  input.setSelectionRange(0, 1)
  await act(async () => {
    await i18n.changeLanguage('zh-CN')
  })
  const translated = screen.getByRole<HTMLInputElement>('textbox', {
    name: '跳转到行:',
  })
  expect(translated.value).toBe('3')
  expect(translated.selectionStart).toBe(0)
  expect(translated.selectionEnd).toBe(1)
  expect(screen.getByRole('button', { name: '跳转' })).toBeTruthy()
  await act(async () => {
    await i18n.changeLanguage('en')
  })
  expect(
    screen.getByRole<HTMLInputElement>('textbox', { name: 'Go to line:' })
      .value,
  ).toBe('3')
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'go' }))
  })
  expect(view.state.doc.lineAt(view.state.selection.main.head).number).toBe(3)
  expect(screen.queryByRole('textbox', { name: 'Go to line:' })).toBeNull()
})
