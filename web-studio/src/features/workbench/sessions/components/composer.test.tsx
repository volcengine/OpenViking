// @vitest-environment jsdom
import type { ReactNode } from 'react'

import { Composer } from '#/features/workbench/sessions/components/composer'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

// Exercise Studio submission semantics; the shared editor is checked in the browser.
vi.mock('@openviking/react/components/viking-bot', () => ({
  VikingBotComposer: ({
    value,
    onValueChange,
    onSend,
    labels,
    actions,
  }: {
    value: string
    onValueChange: (value: string) => void
    onSend: () => void
    labels: { placeholder: string }
    actions: ReactNode
  }) => (
    <>
      <textarea
        placeholder={labels.placeholder}
        value={value}
        onChange={(event) => onValueChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') onSend()
        }}
      />
      {actions}
    </>
  ),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

describe('Composer', () => {
  it('sends text while streaming and keeps the cancel control available', async () => {
    const onSend = vi.fn()
    const onCancel = vi.fn()

    render(<Composer onSend={onSend} onCancel={onCancel} isStreaming />)

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
    expect(screen.getByRole('button', { name: 'chat.cancel' })).toBeTruthy()
  })
})
