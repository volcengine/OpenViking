import { mermaid } from '@streamdown/mermaid'
import { Loader2, TriangleAlert } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useTheme } from 'next-themes'

type RenderState =
  | { status: 'loading' }
  | { message: string; status: 'error' }
  | { status: 'ready'; svg: string; width: number }

function prepareSvg(svg: string): { svg: string; width: number } {
  const document = new DOMParser().parseFromString(svg, 'image/svg+xml')
  const root = document.documentElement
  if (root.localName !== 'svg') {
    throw new Error('Mermaid returned invalid SVG markup.')
  }

  const viewBox = root
    .getAttribute('viewBox')
    ?.trim()
    .split(/[\s,]+/)
    .map(Number)
  const viewBoxWidth = viewBox?.length === 4 ? viewBox[2] : undefined
  const width =
    typeof viewBoxWidth === 'number' &&
    Number.isFinite(viewBoxWidth) &&
    viewBoxWidth > 0
      ? Math.ceil(viewBoxWidth)
      : 640

  root.setAttribute('aria-hidden', 'true')
  root.setAttribute('focusable', 'false')
  root.setAttribute(
    'style',
    `${root.getAttribute('style') ?? ''}; width: 100%; height: auto; max-width: none; display: block;`,
  )

  return {
    svg: new XMLSerializer().serializeToString(root),
    width,
  }
}

export function MermaidDiagram({ chart }: { chart: string }) {
  const { t } = useTranslation('resources')
  const { resolvedTheme } = useTheme()
  const reactId = useId().replace(/[^a-zA-Z0-9_-]/g, '') || 'diagram'
  const renderCount = useRef(0)
  const [state, setState] = useState<RenderState>({ status: 'loading' })
  const theme = resolvedTheme === 'dark' ? 'dark' : 'default'
  const unknownError = t('filePreview.mermaid.unknownError')

  useEffect(() => {
    let active = true
    const renderId = `mermaid-${reactId}-${++renderCount.current}`
    setState({ status: 'loading' })

    void mermaid
      .getMermaid({ theme })
      .render(renderId, chart)
      .then(({ svg }) => {
        if (active) {
          setState({ status: 'ready', ...prepareSvg(svg) })
        }
      })
      .catch((error: unknown) => {
        if (!active) return
        const message =
          error instanceof Error && error.message ? error.message : unknownError
        setState({ message, status: 'error' })
      })

    return () => {
      active = false
    }
  }, [chart, reactId, theme, unknownError])

  if (state.status === 'loading') {
    return (
      <div
        aria-live="polite"
        className="my-4 flex min-h-28 items-center justify-center gap-2 rounded-md border bg-background p-4 text-sm text-muted-foreground"
        role="status"
      >
        <Loader2 aria-hidden="true" className="size-4 animate-spin" />
        <span>{t('filePreview.mermaid.loading')}</span>
      </div>
    )
  }

  if (state.status === 'error') {
    return (
      <div
        className="my-4 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm"
        role="alert"
      >
        <div className="flex items-start gap-2 text-destructive">
          <TriangleAlert
            aria-hidden="true"
            className="mt-0.5 size-4 shrink-0"
          />
          <div className="min-w-0 flex-1">
            <p className="font-medium">
              {t('filePreview.mermaid.renderFailed')}
            </p>
            <p className="mt-1 break-words font-mono text-xs">
              <span className="sr-only">
                {t('filePreview.mermaid.errorDetails')}:{' '}
              </span>
              {state.message}
            </p>
          </div>
        </div>
        <details className="mt-3">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground">
            {t('filePreview.mermaid.showSource')}
          </summary>
          <pre className="mt-2 overflow-x-auto rounded-md bg-muted/60 p-3 text-xs text-foreground">
            {chart}
          </pre>
        </details>
      </div>
    )
  }

  return (
    <div className="my-4 min-w-0 max-w-full overflow-x-auto rounded-md border bg-background p-4">
      <div className="mx-auto" style={{ width: `${state.width}px` }}>
        <div
          aria-label={t('filePreview.mermaid.diagramLabel')}
          data-mermaid-theme={theme}
          dangerouslySetInnerHTML={{ __html: state.svg }}
          role="img"
        />
      </div>
    </div>
  )
}
