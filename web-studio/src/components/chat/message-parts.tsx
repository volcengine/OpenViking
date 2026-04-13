import { Streamdown } from 'streamdown'
import { code } from '@streamdown/code'
import { cjk } from '@streamdown/cjk'
import {
  CheckCircle2Icon,
  CircleAlertIcon,
  LoaderIcon,
  WrenchIcon,
} from 'lucide-react'

import { cn } from '#/lib/utils'

const plugins = { code, cjk }

// ---------------------------------------------------------------------------
// MarkdownContent
// ---------------------------------------------------------------------------

interface MarkdownContentProps {
  content: string
  isStreaming?: boolean
}

export function MarkdownContent({ content, isStreaming }: MarkdownContentProps) {
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
  if (!reasoning) return null

  return (
    <details className="mb-3 rounded-lg border border-border/30 bg-muted/20" open={isRunning}>
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs font-medium text-muted-foreground select-none">
        {isRunning && <LoaderIcon className="size-3 animate-spin" />}
        <span>{isRunning ? '思考中...' : '思考过程'}</span>
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
}

export function ToolCallBlock({ toolName, args, result, isError, isRunning }: ToolCallBlockProps) {
  return (
    <details className="my-2 rounded-lg border border-border/30 bg-muted/20">
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-xs select-none">
        <ToolStatusIcon isRunning={isRunning} isError={isError} />
        <WrenchIcon className="size-3 text-muted-foreground/60" />
        <span className="font-mono font-medium text-foreground/80">{toolName}</span>
        <span className="ml-auto text-muted-foreground/60 text-[11px]">
          {isRunning ? '执行中...' : isError ? '失败' : '完成'}
        </span>
      </summary>
      <div className="space-y-2 border-t border-border/30 px-3 py-2">
        {args && Object.keys(args).length > 0 && (
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/50">
              输入
            </div>
            <pre className="overflow-x-auto rounded-md bg-muted/50 p-2 text-xs leading-relaxed">
              {JSON.stringify(args, null, 2)}
            </pre>
          </div>
        )}
        {result !== undefined && (
          <div>
            <div className="mb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/50">
              结果
            </div>
            <pre
              className={cn(
                'max-h-48 overflow-x-auto overflow-y-auto rounded-md p-2 text-xs leading-relaxed',
                isError ? 'bg-destructive/10 text-destructive' : 'bg-muted/50',
              )}
            >
              {result}
            </pre>
          </div>
        )}
      </div>
    </details>
  )
}

function ToolStatusIcon({ isRunning, isError }: { isRunning: boolean; isError?: boolean }) {
  if (isRunning) return <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
  if (isError) return <CircleAlertIcon className="size-3 text-destructive" />
  return <CheckCircle2Icon className="size-3 text-primary/70" />
}
