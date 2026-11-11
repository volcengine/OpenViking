// @vitest-environment jsdom
import type { ReactNode } from 'react'

import { Composer } from '#/features/workbench/sessions/components/composer'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

// Exercise Studio submission semantics; the shared editor is checked in the browser.
vi.mock('@openviking/react/components/viking-bot', () => ({
  VikingBotComposer: ({
    value,
    onValueChange,
    onSend,
    onCancel,
    isSending,
    labels,
    actions,
  }: {
    value: string
    onValueChange: (value: string) => void
    onSend: () => void
    onCancel: () => void
    isSending: boolean
    labels: { placeholder: string; stop: string }
    actions: ReactNode
  }) => (
    <>
      <textarea
        placeholder={labels.placeholder}
        value={value}
        onChange={(event) => onValueChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && !isSending) onSend()
        }}
      />
      {actions}
      {isSending && <button onClick={onCancel}>{labels.stop}</button>}
    </>
  ),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

afterEach(cleanup)

describe('Composer', () => {
  it('sends text while streaming and keeps the cancel control available', async () => {
    const onSend = vi.fn()
    const onCancel = vi.fn()

    render(<Composer onSend={onSend} onCancel={onCancel} isStreaming />)

    fireEvent.click(screen.getByRole('button', { name: 'chat.cancel' }))
    fireEvent.change(
      await screen.findByPlaceholderText(
        'chat.placeholder',
        {},
        { timeout: 10000 },
      ),
      {
        target: { value: 'follow up' },
      },
    )
    fireEvent.keyDown(screen.getByPlaceholderText('chat.placeholder'), {
      key: 'Enter',
      shiftKey: false,
    })

    expect(onSend).toHaveBeenCalledWith('follow up')
    fireEvent.click(screen.getByRole('button', { name: 'chat.cancel' }))
    expect(onCancel).toHaveBeenCalledTimes(2)
  })

  it('preserves a new draft typed while the previous submission is pending', async () => {
    let accept!: (accepted: boolean) => void
    const onSend = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          accept = resolve
        }),
    )
    render(<Composer onSend={onSend} onCancel={vi.fn()} isStreaming={false} />)

    const input = screen.getByPlaceholderText('chat.placeholder')
    fireEvent.change(input, { target: { value: 'first message' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.change(input, { target: { value: 'new draft' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await act(async () => accept(true))

    expect(onSend).toHaveBeenCalledExactlyOnceWith('first message')
    expect((input as HTMLTextAreaElement).value).toBe('new draft')
  })
})
