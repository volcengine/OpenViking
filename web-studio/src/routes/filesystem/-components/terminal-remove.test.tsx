// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { TerminalPanel } from './terminal-panel'
import type * as OvClient from '#/lib/ov-client'

const m = vi.hoisted(() => ({
  remove: vi.fn(),
  mkdir: vi.fn(),
  move: vi.fn(),
  copy: vi.fn(),
  refresh: vi.fn(),
  identity: '',
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    i18n: { language: 'en' },
    t: (key: string, args?: { uri?: string }) =>
      args?.uri ? `${key}: ${args.uri}` : key,
  }),
}))
vi.mock('@tanstack/react-query', () => ({ useQuery: () => ({}) }))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: m.identity }),
}))
vi.mock('#/routes/resources/-hooks/viking-fm', () => ({
  useInvalidateVikingFs: () => ({ invalidateAll: m.refresh }),
}))
vi.mock('#/lib/ov-client', async (importOriginal) => ({
  ...(await importOriginal<typeof OvClient>()),
  deleteFs: m.remove,
  postFsMkdir: m.mkdir,
  postFsMv: m.move,
  ovClient: { client: { post: m.copy } },
  getOvResult: async (result: Promise<unknown>) => result,
}))
beforeEach(() => {
  m.identity = crypto.randomUUID()
  window.localStorage.clear()
  window.sessionStorage.clear()
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.scrollTo = vi.fn()
  m.remove.mockReset().mockResolvedValue({})
  m.mkdir.mockReset().mockResolvedValue({})
  m.move.mockReset().mockResolvedValue({})
  m.copy.mockReset().mockResolvedValue({})
  m.refresh.mockReset().mockResolvedValue(undefined)
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})
function submit(command: string) {
  render(
    <TerminalPanel
      currentUri="viking://resources"
      entries={[]}
      openingUri={null}
      onOpenResource={vi.fn()}
      onSessionChange={vi.fn()}
      toolbarContainer={null}
    />,
  )
  const input = screen.getByRole('textbox')
  fireEvent.change(input, { target: { value: command } })
  fireEvent.submit(input.closest('form')!)
}
it('deletes the explicit subtree only after confirmation, then refreshes the tree', async () => {
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  submit('/rm viking://resources/old --recursive')
  await waitFor(() => expect(m.refresh).toHaveBeenCalledTimes(1))
  expect(confirm).toHaveBeenCalledWith(
    'terminal.rmConfirmRecursive: viking://resources/old',
  )
  expect(m.remove).toHaveBeenCalledExactlyOnceWith({
    query: { uri: 'viking://resources/old', recursive: true },
  })
})
it('does not send a deletion request when confirmation is cancelled', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(false)
  submit('/rm viking://resources/old')
  await screen.findByText('terminal.rmCancelled')
  expect(m.remove).not.toHaveBeenCalled()
  expect(m.refresh).not.toHaveBeenCalled()
})
it.each([
  'old notes.md',
  'old"notes.md',
  "old'notes.md",
  'old\\notes.md',
  'old$notes;archive.md',
])('preserves the selected deletion URI when completing %s', async (name) => {
  const uri = `viking://resources/${name}`
  const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
  render(
    <TerminalPanel
      currentUri="viking://resources"
      entries={[
        {
          uri,
          name,
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
  const input = screen.getByRole('textbox')
  fireEvent.focus(input)
  fireEvent.change(input, { target: { value: '/rm viking://resources/old' } })
  fireEvent.keyDown(input, { key: 'Tab' })
  fireEvent.submit(input.closest('form')!)
  await waitFor(() => expect(m.refresh).toHaveBeenCalledTimes(1))
  expect(confirm).toHaveBeenCalledWith(`terminal.rmConfirm: ${uri}`)
  expect(m.remove).toHaveBeenCalledExactlyOnceWith({
    query: { uri, recursive: false },
  })
})
it.each([
  '/rm --recursive',
  '/rm viking:// -r',
  '/rm viking:/// -r',
  '/rm viking://// --recursive',
])(
  'rejects missing or root deletion targets before confirmation: %s',
  async (command) => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    submit(command)
    await screen.findByText('terminal.rmUsage')
    expect(confirm).not.toHaveBeenCalled()
    expect(m.remove).not.toHaveBeenCalled()
    expect(m.refresh).not.toHaveBeenCalled()
  },
)
it('shows a failed deletion without reporting success or refreshing', async () => {
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  m.remove.mockRejectedValue(new Error('permission denied'))
  submit('/rm viking://resources/old')
  await screen.findByText('permission denied')
  expect(m.refresh).not.toHaveBeenCalled()
  expect(
    screen.queryByText('terminal.rmDeleted: viking://resources/old'),
  ).toBeNull()
})

it('creates a directory with its description and refreshes the tree', async () => {
  submit('/mkdir "viking://resources/new docs" --description "Project notes"')
  await waitFor(() => expect(m.refresh).toHaveBeenCalledTimes(1))
  expect(m.mkdir).toHaveBeenCalledExactlyOnceWith({
    body: { uri: 'viking://resources/new docs', description: 'Project notes' },
  })
})
it('moves to the explicit destination and refreshes the tree', async () => {
  submit('/mv viking://resources/old.md viking://resources/new.md')
  await waitFor(() => expect(m.refresh).toHaveBeenCalledTimes(1))
  expect(m.move).toHaveBeenCalledExactlyOnceWith({
    body: {
      from_uri: 'viking://resources/old.md',
      to_uri: 'viking://resources/new.md',
    },
  })
})
it('copies a directory through the authenticated API client with recursion', async () => {
  submit('/cp viking://resources/docs viking://resources/docs-copy -r')
  await waitFor(() => expect(m.refresh).toHaveBeenCalledTimes(1))
  expect(m.copy).toHaveBeenCalledExactlyOnceWith({
    url: '/api/v1/fs/cp',
    body: {
      from_uri: 'viking://resources/docs',
      to_uri: 'viking://resources/docs-copy',
      recursive: true,
    },
    headers: { 'Content-Type': 'application/json' },
  })
})
it('does not infer a missing move destination from the current directory', async () => {
  submit('/mv viking://resources/old.md')
  await screen.findByText('terminal.mvUsage')
  expect(m.move).not.toHaveBeenCalled()
  expect(m.refresh).not.toHaveBeenCalled()
})
it('surfaces a copy conflict without reporting success or refreshing', async () => {
  m.copy.mockRejectedValue(new Error('destination exists'))
  submit('/cp viking://resources/docs viking://resources/docs-copy')
  await screen.findByText('destination exists')
  expect(m.refresh).not.toHaveBeenCalled()
  expect(screen.queryByText('terminal.cpCompleted')).toBeNull()
})
