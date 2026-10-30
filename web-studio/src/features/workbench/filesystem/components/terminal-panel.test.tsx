// @vitest-environment jsdom

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { TerminalHistoryItem } from '#/features/workbench/filesystem/components/terminal-panel'

afterEach(() => {
  cleanup()
})

describe('TerminalHistoryItem', () => {
  it('shows the full command in a right-aligned bubble', () => {
    const command =
      'ov add-resource https://example.com/spec.md --to viking://resources/spec'
    const { container } = render(
      <TerminalHistoryItem
        entry={{ id: 'command-1', kind: 'command', title: command }}
        onOpenResource={vi.fn()}
        openingUri={null}
      />,
    )

    expect(screen.getByText(command)).not.toBeNull()
    expect(container.querySelector('.justify-end')).not.toBeNull()
  })
})
