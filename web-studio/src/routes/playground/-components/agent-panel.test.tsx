// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { AgentToolbar } from './agent-panel'

afterEach(() => {
  cleanup()
  document.body.innerHTML = ''
})

describe('AgentToolbar', () => {
  it('portals the active session title beside the panel controls', () => {
    const container = document.createElement('div')
    const onNewSession = vi.fn()
    const onOpenHistory = vi.fn()
    document.body.append(container)

    render(
      <AgentToolbar
        container={container}
        historyLabel="History"
        isCreatingSession={false}
        newSessionLabel="New session"
        onNewSession={onNewSession}
        onOpenHistory={onOpenHistory}
        sessionTitle="Current session"
      />,
    )

    expect(container.textContent).toContain('Current session')
    fireEvent.click(screen.getByTitle('History'))
    fireEvent.click(screen.getByTitle('New session'))
    expect(onOpenHistory).toHaveBeenCalledOnce()
    expect(onNewSession).toHaveBeenCalledOnce()

    container.remove()
  })
})
