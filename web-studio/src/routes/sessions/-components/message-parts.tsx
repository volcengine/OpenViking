import { Streamdown } from 'streamdown'
import { code } from '@streamdown/code'
import { cjk } from '@streamdown/cjk'
import { useTranslation } from 'react-i18next'
import {
  CheckCircle2Icon,
  CircleAlertIcon,
  FileTextIcon,
  LoaderIcon,
  WrenchIcon,
} from 'lucide-react'

import { cn } from '#/lib/utils'
import { cleanVikingUri, VIKING_URI_RE } from '#/lib/viking-uri'

const plugins = { code, cjk }

// ---------------------------------------------------------------------------
// MarkdownContent
// ---------------------------------------------------------------------------

interface MarkdownContentProps {
  content: string
  isStreaming?: boolean
}

export function MarkdownContent({
  content,
  isStreaming,
}: MarkdownContentProps) {
  if (!content) return null

  return (
    <div
      className={cn(
        'prose prose-sm dark:prose-invert max-w-none',
        // Headings
        'prose-headings:font-semibold prose-headings:tracking-tight',
        'prose-h1:text-lg prose-h2:text-base prose-h3:text-sm',
        'prose-h1:mt-6 prose-h1:mb-3 prose-h2:mt-5 prose-h2:mb-2 prose-h3:mt-4 prose-h3:mb-2',
        'first:prose-headings:mt-0',
        // Paragraphs
        'prose-p:leading-relaxed prose-p:my-2',
        // Links
        'prose-a:text-primary prose-a:no-underline hover:prose-a:underline prose-a:font-medium',
        // Lists
        'prose-li:my-0.5',
        // Code inline
        'prose-code:before:content-none prose-code:after:content-none',
        'prose-code:rounded prose-code:bg-muted prose-code:px-1.5 prose-code:py-0.5 prose-code:text-[13px] prose-code:font-normal',
        // Blockquote
        'prose-blockquote:border-l-primary/40 prose-blockquote:bg-muted/30 prose-blockquote:rounded-r-lg prose-blockquote:py-1 prose-blockquote:px-4 prose-blockquote:not-italic',
        // Tables
        'prose-th:text-left prose-th:text-xs prose-th:font-semibold prose-th:uppercase prose-th:tracking-wider prose-th:text-muted-foreground',
        'prose-td:text-sm',
        // HR
        'prose-hr:border-border/50',
        // Strong
        'prose-strong:font-semibold',
      )}
    >
      <Streamdown plugins={plugins} isAnimating={isStreaming}>
        {content}
      </Streamdown>
    </div>
  )
}

// ---------------------------------------------------------------------------
// ReasoningBlock
// ---------------------------------------------------------------------------

interface ReasoningBlockProps {
  reasoning: string
  isRunning: boolean
}

export function ReasoningBlock({ reasoning, isRunning }: ReasoningBlockProps) {
  const { t } = useTranslation('sessions')

  if (!reasoning) return null

  return (
    <details
      className="mb-3 rounded-lg border border-border/30 bg-muted/20"
      open={isRunning}
    >
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs font-medium text-muted-foreground select-none">
        {isRunning && <LoaderIcon className="size-3 animate-spin" />}
        <span>{isRunning ? t('chat.thinking') : t('chat.reasoning')}</span>
      </summary>
      <div className="border-t border-border/30 px-3 py-2 text-xs text-muted-foreground/80 leading-relaxed whitespace-pre-wrap">
        {reasoning}
      </div>
    </details>
  )
}

// ---------------------------------------------------------------------------
// ToolCallBlock
// ---------------------------------------------------------------------------

interface ToolCallBlockProps {
  toolName: string
  args?: Record<string, unknown>
  result?: string
  isError?: boolean
  isRunning: boolean
  onResourceClick?: (uri: string) => void
}

export function ToolCallBlock({
  toolName,
  args,
  result,
  isError,
  isRunning,
  onResourceClick,
}: ToolCallBlockProps) {
  const { t } = useTranslation('sessions')
  const refs = extractVikingUris(result)

  return (
    <details className="my-2 rounded-lg border border-border/30 bg-muted/20">
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-xs select-none">
        <ToolStatusIcon isRunning={isRunning} isError={isError} />
        <WrenchIcon className="size-3 text-muted-foreground/60" />
        <span className="font-mono font-medium text-foreground/80">
          {toolName}
        </span>
        <span className="ml-auto text-muted-foreground/60 text-[11px]">
          {isRunning
            ? t('chat.toolStatus.running')
            : isError
              ? t('chat.toolStatus.failed')
              : t('chat.toolStatus.completed')}
        </span>
      </summary>
      <div className="space-y-2 border-t border-border/30 px-3 py-2">
        {args && Object.keys(args).length > 0 && (
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/50">
              {t('chat.toolInput')}
            </div>
            <pre className="overflow-x-auto rounded-md bg-muted/50 p-2 text-xs leading-relaxed">
              {JSON.stringify(args, null, 2)}
            </pre>
          </div>
        )}
        {result !== undefined && (
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/50">
              {t('chat.toolResult')}
            </div>
            <pre
              className={cn(
                'max-h-48 overflow-x-auto overflow-y-auto rounded-md p-2 text-xs leading-relaxed',
                isError ? 'bg-destructive/10 text-destructive' : 'bg-muted/50',
              )}
            >
              {result}
            </pre>
            {refs.length > 0 && onResourceClick ? (
              <div className="mt-2 grid gap-1.5">
                {refs.map((uri) => (
                  <button
                    key={uri}
                    type="button"
                    className="flex min-w-0 items-center gap-2 rounded-md border bg-background px-2 py-1.5 text-left transition-colors hover:border-primary/50 hover:bg-primary/5"
                    onClick={() => onResourceClick(uri)}
                  >
                    <FileTextIcon className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-primary">
                      {uri}
                    </span>
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        )}
      </div>
    </details>
  )
}

function ToolStatusIcon({
  isRunning,
  isError,
}: {
  isRunning: boolean
  isError?: boolean
}) {
  if (isRunning)
    return <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
  if (isError) return <CircleAlertIcon className="size-3 text-destructive" />
  return <CheckCircle2Icon className="size-3 text-primary/70" />
}

function extractVikingUris(text: string | undefined): string[] {
  if (!text) return []
  const seen = new Set<string>()

  const parsed = parseJsonResult(text)
  if (parsed !== undefined) {
    collectStructuredUris(parsed, seen)
  }

  // Always scan the raw text too: URIs embedded in free-text fields or under
  // keys other than `uri` are invisible to the structured pass. The Set keeps
  // the two passes deduped.
  const matches = text.match(VIKING_URI_RE) ?? []
  for (const match of matches) {
    const uri = cleanVikingUri(match)
    if (uri) seen.add(uri)
  }
  return [...seen]
}

function parseJsonResult(text: string): unknown {
  const trimmed = text.trim()
  if (!trimmed) return undefined

  try {
    return JSON.parse(trimmed) as unknown
  } catch {
    return undefined
  }
}

function collectStructuredUris(value: unknown, seen: Set<string>): void {
  if (Array.isArray(value)) {
    for (const item of value) collectStructuredUris(item, seen)
    return
  }

  if (!value || typeof value !== 'object') return

  for (const [key, nested] of Object.entries(value)) {
    if (key === 'uri' && typeof nested === 'string') {
      const uri = cleanVikingUri(nested)
      if (uri) seen.add(uri)
      continue
    }

    if (Array.isArray(nested) || (nested && typeof nested === 'object')) {
      collectStructuredUris(nested, seen)
    }
  }
}
