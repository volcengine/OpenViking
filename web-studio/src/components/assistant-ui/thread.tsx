import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useMessage,
} from '@assistant-ui/react'
import type {
  ToolCallMessagePartProps,
  ReasoningMessagePartProps,
} from '@assistant-ui/react'
import { MarkdownTextPrimitive } from '@assistant-ui/react-markdown'
import {
  ArrowUpIcon,
  BotIcon,
  CheckCircle2Icon,
  CircleAlertIcon,
  CompassIcon,
  LoaderIcon,
  SquareIcon,
  UserIcon,
  WrenchIcon,
} from 'lucide-react'

import { cn } from '#/lib/utils'
import { useSessionTitles } from '#/routes/sessions/-hooks/use-session-titles'

export function Thread({ sessionId }: { sessionId?: string | null }) {
  const { getTitle } = useSessionTitles()
  const title = sessionId ? getTitle(sessionId) : undefined

  return (
    <ThreadPrimitive.Root className="flex h-full flex-col">
      {title && (
        <div className="flex h-12 items-center border-b border-border px-6">
          <h2 className="text-sm font-medium truncate text-foreground">{title}</h2>
        </div>
      )}
      <ThreadPrimitive.Viewport className="flex flex-1 flex-col items-center overflow-y-auto scroll-smooth px-4 pt-12 pb-4">
        <ThreadPrimitive.Empty>
          <ThreadEmpty />
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages
          components={{
            UserMessage,
            AssistantMessage,
          }}
        />
      </ThreadPrimitive.Viewport>
      <Composer />
    </ThreadPrimitive.Root>
  )
}

function ThreadEmpty() {
  return (
    <div className="flex grow flex-col items-center justify-center gap-3">
      <div className="flex size-10 items-center justify-center rounded-full bg-muted">
        <CompassIcon className="size-5 text-muted-foreground" />
      </div>
      <p className="text-sm text-muted-foreground">
        Start a conversation to explore your knowledge base.
      </p>
    </div>
  )
}

function Composer() {
  return (
    <div className="border-t border-border bg-background px-4 py-3">
      <ComposerPrimitive.Root className="mx-auto flex w-full max-w-2xl items-end gap-2">
        <ComposerPrimitive.Input
          autoFocus
          placeholder="Type a message..."
          rows={1}
          className={cn(
            'min-h-[40px] flex-1 resize-none rounded-lg px-3 py-2 text-sm',
            'border border-input bg-transparent',
            'ring-offset-background placeholder:text-muted-foreground',
            'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          )}
        />
        <ThreadPrimitive.If running={false}>
          <ComposerPrimitive.Send
            className={cn(
              'inline-flex size-9 shrink-0 items-center justify-center rounded-lg',
              'bg-primary text-primary-foreground shadow-sm',
              'transition-colors hover:bg-primary/90 disabled:opacity-50',
            )}
          >
            <ArrowUpIcon className="size-4" />
          </ComposerPrimitive.Send>
        </ThreadPrimitive.If>
        <ThreadPrimitive.If running>
          <ComposerPrimitive.Cancel
            className={cn(
              'inline-flex size-9 shrink-0 items-center justify-center rounded-lg',
              'bg-destructive text-destructive-foreground shadow-sm',
              'transition-colors hover:bg-destructive/90',
            )}
          >
            <SquareIcon className="size-4" />
          </ComposerPrimitive.Cancel>
        </ThreadPrimitive.If>
      </ComposerPrimitive.Root>
    </div>
  )
}

function UserMessage() {
  return (
    <MessagePrimitive.Root className="mb-6 flex w-full max-w-2xl gap-3 justify-end">
      <div className="rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm text-primary-foreground">
        <MessagePrimitive.Content />
      </div>
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
        <UserIcon className="size-3.5 text-primary" />
      </div>
    </MessagePrimitive.Root>
  )
}

function AssistantMessage() {
  // iteration is only present on streaming messages via metadata.custom
  // useMessage with selector avoids full re-renders
  // eslint-disable-next-line @typescript-eslint/no-deprecated
  const iteration = useMessage((s) => {
    const custom = (s.metadata as { custom?: { iteration?: number } } | undefined)?.custom
    return custom?.iteration
  })

  return (
    <MessagePrimitive.Root className="mb-6 flex w-full max-w-2xl gap-3 items-start">
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted">
        <BotIcon className="size-3.5 text-muted-foreground" />
      </div>
      <div className="max-w-full min-w-0 flex-1 text-sm">
        {iteration != null && iteration > 1 && (
          <div className="mb-2">
            <span className="inline-flex items-center rounded-md bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
              Iteration {iteration}
            </span>
          </div>
        )}
        <MessagePrimitive.Content
          components={{
            Text: AssistantText,
            Reasoning: AssistantReasoning,
            tools: {
              Fallback: AssistantToolCall,
            },
          }}
        />
      </div>
    </MessagePrimitive.Root>
  )
}

function AssistantText() {
  return (
    <div className="prose prose-sm dark:prose-invert max-w-none">
      <MarkdownTextPrimitive />
    </div>
  )
}

function AssistantReasoning({ status }: ReasoningMessagePartProps) {
  const isRunning = status.type === 'running'

  return (
    <details className="mb-3 rounded-lg border border-border/40 bg-muted/30" open={isRunning}>
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs font-medium text-muted-foreground select-none">
        {isRunning && (
          <LoaderIcon className="size-3 animate-spin" />
        )}
        <span>{isRunning ? 'Thinking...' : 'Thought process'}</span>
      </summary>
      <div className="border-t border-border/40 px-3 py-2 text-xs text-muted-foreground whitespace-pre-wrap">
        <MarkdownTextPrimitive />
      </div>
    </details>
  )
}

function AssistantToolCall({ toolName, args, result, isError, status }: ToolCallMessagePartProps) {
  const isRunning = status.type === 'running'

  return (
    <details className="my-2 rounded-lg border border-border/40 bg-muted/30">
      <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-xs select-none">
        <ToolStatusIcon isRunning={isRunning} isError={isError} />
        <WrenchIcon className="size-3 text-muted-foreground" />
        <span className="font-mono font-medium text-foreground">{toolName}</span>
        <span className="ml-auto text-muted-foreground">
          {isRunning ? 'Running...' : isError ? 'Failed' : 'Done'}
        </span>
      </summary>
      <div className="space-y-2 border-t border-border/40 px-3 py-2">
        {args && Object.keys(args).length > 0 && (
          <div>
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Input</div>
            <pre className="overflow-x-auto rounded-md bg-muted p-2 text-xs">
              {JSON.stringify(args, null, 2)}
            </pre>
          </div>
        )}
        {result !== undefined && (
          <div>
            <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">Result</div>
            <pre className={cn(
              'max-h-48 overflow-x-auto overflow-y-auto rounded-md p-2 text-xs',
              isError ? 'bg-destructive/10 text-destructive' : 'bg-muted',
            )}>
              {typeof result === 'string' ? result : JSON.stringify(result, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </details>
  )
}

function ToolStatusIcon({ isRunning, isError }: { isRunning: boolean; isError?: boolean }) {
  if (isRunning) {
    return <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
  }
  if (isError) {
    return <CircleAlertIcon className="size-3 text-destructive" />
  }
  return <CheckCircle2Icon className="size-3 text-primary" />
}
