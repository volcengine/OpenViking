// @vitest-environment jsdom

import { Composer } from '#/features/workbench/sessions/components/composer'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

describe('Composer', () => {
  it('sends text while streaming and keeps the cancel control available', () => {
    const onSend = vi.fn()
    const onCancel = vi.fn()

    render(<Composer onSend={onSend} onCancel={onCancel} isStreaming />)

    fireEvent.change(screen.getByLabelText('chat.placeholder'), {
      target: { value: 'follow up' },
    })
    fireEvent.keyDown(screen.getByLabelText('chat.placeholder'), {
      key: 'Enter',
      shiftKey: false,
    })

    expect(onSend).toHaveBeenCalledWith('follow up')
    expect(screen.getByRole('button', { name: 'chat.cancel' })).toBeTruthy()
  })
})
