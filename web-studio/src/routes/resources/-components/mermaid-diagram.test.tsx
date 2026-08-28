// @vitest-environment jsdom

import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MermaidDiagram } from './mermaid-diagram'

class ImmediateIntersectionObserver {
  disconnect() {}
  observe(target: Element) {
    this.callback(
      [{ isIntersecting: true, target } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    )
  }
  takeRecords() {
    return []
  }
  unobserve() {}

  constructor(private readonly callback: IntersectionObserverCallback) {}
}

describe('MermaidDiagram', () => {
  beforeEach(() => {
    vi.stubGlobal('IntersectionObserver', ImmediateIntersectionObserver)
    Object.defineProperty(SVGElement.prototype, 'getBBox', {
      configurable: true,
      value: () => ({
        bottom: 20,
        height: 20,
        left: 0,
        right: 100,
        top: 0,
        width: 100,
        x: 0,
        y: 0,
      }),
    })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    Reflect.deleteProperty(SVGElement.prototype, 'getBBox')
  })

  it('renders a real Mermaid flowchart as SVG', async () => {
    render(<MermaidDiagram chart={'graph TD\n    A[Client] --> B[Server]'} />)

    const diagram = await screen.findByRole('img', {
      name: 'Mermaid chart',
    })
    await waitFor(() => {
      expect(diagram.querySelector('svg')).not.toBeNull()
    })
    expect(diagram.textContent).toContain('Client')
    expect(diagram.textContent).toContain('Server')
  })
})
