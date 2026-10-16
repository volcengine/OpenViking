// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TerminalHistoryItem } from './terminal-panel'

afterEach(() => {
  cleanup()
})

describe('TerminalHistoryItem', () => {
  it('renders command entries with their terminal icon', () => {
    const { container } = render(
      <TerminalHistoryItem
        entry={{ id: 'command-1', kind: 'command', title: '/ls' }}
        onOpenResource={vi.fn()}
        openingUri={null}
      />,
    )

    expect(screen.getByText('/ls')).not.toBeNull()
    expect(container.querySelector('svg')).not.toBeNull()
  })
})
