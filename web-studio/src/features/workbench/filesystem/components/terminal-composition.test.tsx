// @vitest-environment jsdom
import { TerminalPanel } from '#/features/workbench/filesystem/components/terminal-panel'
import userEvent from '@testing-library/user-event'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const read = vi.hoisted(() => vi.fn())
vi.mock('#/features/workbench/resources/lib/api', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  fetchFileContent: read,
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ i18n: { language: 'en' }, t: (key: string) => key }),
}))
vi.mock('@tanstack/react-query', () => ({ useQuery: () => ({}) }))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'composition-test' }),
}))
vi.mock('#/features/workbench/resources/hooks/viking-fm', () => ({
  useInvalidateVikingFs: () => ({ invalidateAll: vi.fn() }),
}))
beforeEach(() => {
  read.mockReset().mockResolvedValue({ content: 'Research notes' })
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.scrollTo = vi.fn()
})
afterEach(cleanup)

function setup() {
  render(
    <TerminalPanel
      currentUri="viking://resources"
      entries={[
        {
          uri: 'viking://resources/研究.md',
          name: '研究.md',
          isDir: false,
          size: '',
          sizeBytes: null,
          modTime: '',
          modTimestamp: null,
          abstract: '',
        },
      ]}
      openingUri={null}
      onOpenResource={vi.fn()}
      onSessionChange={vi.fn()}
      toolbarContainer={null}
    />,
  )
  const input = screen.getByRole<HTMLInputElement>('textbox')
  fireEvent.focus(input)
  fireEvent.change(input, { target: { value: '/read viking://resources/研' } })
  return input
}

it.each(['Enter', 'Tab', 'ArrowRight', 'ArrowDown', 'ArrowUp'])(
  'leaves %s to the IME instead of consuming it for suggestions',
  (key) => {
    const input = setup()
    expect(fireEvent.keyDown(input, { key, isComposing: true })).toBe(true)
    expect(input.value).toBe('/read viking://resources/研')
    // Normal completion remains available once composition ends.
    fireEvent.keyDown(input, { key: 'Tab' })
    expect(input.value).toBe('/read viking://resources/研究.md')
  },
)

it('preserves the candidate-confirming Enter with legacy IME key code 229', () => {
  const input = setup()
  expect(fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 })).toBe(true)
  expect(input.value).toBe('/read viking://resources/研')
})

it('keeps normal Enter completion, Escape dismissal and Enter submission', async () => {
  const input = setup()
  const user = userEvent.setup()
  await user.click(input)
  await user.keyboard('{Enter}')
  expect(input.value).toBe('/read viking://resources/研究.md')
  expect(read).not.toHaveBeenCalled()
  fireEvent.change(input, {
    target: { value: '/read viking://resources/研究' },
  })
  await user.keyboard('{Escape}')
  expect(fireEvent.keyDown(input, { key: 'Tab' })).toBe(true)
  await user.keyboard('{Enter}')
  await waitFor(() =>
    expect(read).toHaveBeenCalledExactlyOnceWith('viking://resources/研究', {
      limit: 1200,
      raw: true,
    }),
  )
})
