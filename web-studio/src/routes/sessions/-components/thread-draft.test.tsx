// @vitest-environment jsdom
import { useState } from 'react'
import { act, cleanup, render } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { Thread } from './thread'

const m = vi.hoisted(() => ({
  create: vi.fn(),
  history: vi.fn(),
  send: vi.fn(),
  onSend: undefined as undefined | ((text: string) => Promise<boolean>),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({ identityScopeKey: 'test' }),
}))
vi.mock('#/lib/sessions/use-session-titles', () => ({
  useSessionTitles: () => ({ getTitle: (id: string) => id }),
}))
vi.mock('#/lib/sessions/use-sessions', () => ({
  useCreateSession: () => ({ mutateAsync: m.create }),
  useSession: () => ({}),
  useSessionMessages: m.history,
}))
vi.mock('#/lib/sessions/use-chat', () => ({
  useChat: () => ({ messages: [], status: 'idle', send: m.send }),
}))
vi.mock('./memory-impact', () => ({ MemoryImpact: () => null }))
vi.mock('./composer', () => ({
  Composer: ({ onSend }: { onSend: typeof m.onSend }) => {
    m.onSend = onSend
    return null
  },
}))

beforeEach(() => {
  vi.clearAllMocks()
  m.history.mockReturnValue({ isPending: true })
  Element.prototype.scrollIntoView = vi.fn()
})
afterEach(cleanup)

it('does not request draft history and persists only once on first send', async () => {
  m.create.mockResolvedValue({ session_id: 'draft' })
  render(<Thread sessionId="draft" draft />)
  expect(m.create).not.toHaveBeenCalled()
  expect(m.history).toHaveBeenLastCalledWith(undefined)
  await act(async () => {
    expect(await m.onSend!('first')).toBe(true)
  })
  await act(async () => {
    await m.onSend!('second')
  })
  expect(m.create).toHaveBeenCalledExactlyOnceWith('draft')
  expect(m.send).toHaveBeenCalledWith('first')
  expect(m.history).toHaveBeenLastCalledWith(undefined)
})

it('rejects failed creation so the composer can retain input', async () => {
  m.create.mockRejectedValue(new Error('unavailable'))
  render(<Thread sessionId="draft" draft />)
  await act(async () => {
    expect(await m.onSend!('first')).toBe(false)
  })
  expect(m.send).not.toHaveBeenCalled()
})

it('does not send after leaving a draft during creation', async () => {
  let resolve!: (value: unknown) => void
  m.create.mockImplementation(
    () =>
      new Promise((done) => {
        resolve = done
      }),
  )
  const view = render(<Thread sessionId="draft" draft />)
  let pending!: Promise<boolean>
  act(() => {
    pending = m.onSend!('first')
  })
  expect(await m.onSend!('duplicate')).toBe(false)
  view.unmount()
  resolve({ session_id: 'draft' })
  expect(await pending).toBe(false)
  expect(m.send).not.toHaveBeenCalled()
  expect(m.create).toHaveBeenCalledTimes(1)
})

it('loads the correct history when switching existing sessions', () => {
  const view = render(<Thread sessionId="first" />)
  expect(m.history).toHaveBeenLastCalledWith('first')
  view.rerender(<Thread sessionId="second" />)
  expect(m.history).toHaveBeenLastCalledWith('second')
  expect(m.create).not.toHaveBeenCalled()
})

it('loads persisted history and continues after switching tabs', async () => {
  let showThread!: (visible: boolean) => void
  function Workspace() {
    const [draft, setDraft] = useState(true)
    const [visible, setVisible] = useState(true)
    showThread = setVisible
    return visible ? (
      <Thread
        sessionId="draft"
        draft={draft}
        onPersisted={() => setDraft(false)}
      />
    ) : null
  }
  m.create
    .mockResolvedValueOnce({ session_id: 'draft' })
    .mockRejectedValue(new Error('Session already exists'))
  render(<Workspace />)
  await act(async () => {
    expect(await m.onSend!('first')).toBe(true)
  })
  act(() => showThread(false))
  act(() => showThread(true))
  expect(m.history).toHaveBeenLastCalledWith('draft')
  await act(async () => {
    expect(await m.onSend!('second')).toBe(true)
  })
  expect(m.create).toHaveBeenCalledExactlyOnceWith('draft')
  expect(m.send).toHaveBeenLastCalledWith('second')
})
