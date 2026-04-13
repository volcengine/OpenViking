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
    <div className="prose prose-sm dark:prose-invert max-w-none">
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
    <details className="mb-3 rounded-lg border border-border/40 bg-muted/30" open={isRunning}>
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs font-medium text-muted-foreground select-none">
        {isRunning && <LoaderIcon className="size-3 animate-spin" />}
        <span>{isRunning ? '思考中...' : '思考过程'}</span>
      </summary>
      <div className="border-t border-border/40 px-3 py-2 text-xs text-muted-foreground whitespace-pre-wrap">
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
    <details className="my-2 rounded-lg border border-border/40 bg-muted/30">
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-xs select-none">
        <ToolStatusIcon isRunning={isRunning} isError={isError} />
        <WrenchIcon className="size-3 text-muted-foreground" />
        <span className="font-mono font-medium text-foreground">{toolName}</span>
        <span className="ml-auto text-muted-foreground">
          {isRunning ? '执行中...' : isError ? '失败' : '完成'}
        </span>
      </summary>
      <div className="space-y-2 border-t border-border/40 px-3 py-2">
        {args && Object.keys(args).length > 0 && (
          <div>
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              输入
            </div>
            <pre className="overflow-x-auto rounded-md bg-muted p-2 text-xs">
              {JSON.stringify(args, null, 2)}
            </pre>
          </div>
        )}
        {result !== undefined && (
          <div>
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
              结果
            </div>
            <pre
              className={cn(
                'max-h-48 overflow-x-auto overflow-y-auto rounded-md p-2 text-xs',
                isError ? 'bg-destructive/10 text-destructive' : 'bg-muted',
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
  return <CheckCircle2Icon className="size-3 text-primary" />
}
