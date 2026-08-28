// @vitest-environment jsdom

import { mermaid } from '@streamdown/mermaid'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { MermaidDiagram } from './mermaid-diagram'

const testContext = vi.hoisted(() => ({
  locale: 'en' as 'en' | 'zh-CN',
  theme: 'light',
  translations: {
    en: {
      'filePreview.mermaid.diagramLabel': 'Mermaid diagram',
      'filePreview.mermaid.errorDetails': 'Error details',
      'filePreview.mermaid.loading': 'Rendering Mermaid diagram...',
      'filePreview.mermaid.renderFailed': 'Unable to render Mermaid diagram.',
      'filePreview.mermaid.showSource': 'Show Mermaid source',
      'filePreview.mermaid.unknownError': 'Unknown Mermaid rendering error.',
    },
    'zh-CN': {
      'filePreview.mermaid.diagramLabel': 'Mermaid 图表',
      'filePreview.mermaid.errorDetails': '错误详情',
      'filePreview.mermaid.loading': '正在渲染 Mermaid 图表...',
      'filePreview.mermaid.renderFailed': '无法渲染 Mermaid 图表。',
      'filePreview.mermaid.showSource': '查看 Mermaid 源码',
      'filePreview.mermaid.unknownError': '未知的 Mermaid 渲染错误。',
    },
  } as Record<'en' | 'zh-CN', Record<string, string>>,
}))

vi.mock('next-themes', () => ({
  useTheme: () => ({ resolvedTheme: testContext.theme }),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) =>
      testContext.translations[testContext.locale][key] ?? key,
  }),
}))

describe('MermaidDiagram', () => {
  beforeEach(() => {
    testContext.locale = 'en'
    testContext.theme = 'light'
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
    cleanup()
    vi.restoreAllMocks()
    Reflect.deleteProperty(SVGElement.prototype, 'getBBox')
  })

  it('renders a real Mermaid flowchart at its view-box width', async () => {
    const getMermaid = vi.spyOn(mermaid, 'getMermaid')

    render(<MermaidDiagram chart={'graph TD\n    A[Client] --> B[Server]'} />)

    expect(screen.getByRole('status').textContent).toContain(
      'Rendering Mermaid diagram...',
    )
    const diagram = await screen.findByRole('img', {
      name: 'Mermaid diagram',
    })
    const svg = diagram.querySelector('svg')
    expect(svg).not.toBeNull()
    expect(diagram.textContent).toContain('Client')
    expect(diagram.textContent).toContain('Server')
    expect(diagram.parentElement?.style.width).toMatch(/^\d+px$/)
    expect(svg?.style.maxWidth).toBe('none')
    expect(getMermaid).toHaveBeenCalledWith({ theme: 'default' })
  })

  it('rerenders with Mermaid dark theme when the app theme changes', async () => {
    const getMermaid = vi.spyOn(mermaid, 'getMermaid')
    const { rerender } = render(
      <MermaidDiagram chart={'graph TD\n    A[Client] --> B[Server]'} />,
    )

    await screen.findByRole('img', { name: 'Mermaid diagram' })
    testContext.theme = 'dark'
    rerender(<MermaidDiagram chart={'graph TD\n    A[Client] --> B[Server]'} />)

    await waitFor(() => {
      expect(
        screen
          .getByRole('img', { name: 'Mermaid diagram' })
          .getAttribute('data-mermaid-theme'),
      ).toBe('dark')
    })
    expect(getMermaid).toHaveBeenCalledWith({ theme: 'dark' })
  })

  it('localizes an error while preserving its diagnostic and source', async () => {
    testContext.locale = 'zh-CN'

    render(<MermaidDiagram chart="this is not a diagram" />)

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('无法渲染 Mermaid 图表。')
    expect(alert.textContent).toContain(
      'No diagram type detected matching given configuration',
    )
    expect(alert.textContent).toContain('查看 Mermaid 源码')
    expect(alert.textContent).toContain('this is not a diagram')
  })
})
