import { BotIcon, UserIcon } from 'lucide-react'

import type { Message } from '#/routes/sessions/-types/message'
import type { StreamToolCall } from '#/routes/sessions/-types/chat'
import { MarkdownContent, ReasoningBlock, ToolCallBlock } from './message-parts'

// ---------------------------------------------------------------------------
// MessageList
// ---------------------------------------------------------------------------

interface MessageListProps {
  messages: Message[]
  streaming?: {
    content: string
    toolCalls: StreamToolCall[]
    reasoning: string
    iteration: number
  }
}

export function MessageList({ messages, streaming }: MessageListProps) {
  return (
    <>
      {messages.map((msg) =>
        msg.role === 'user' ? (
          <UserMessage key={msg.id} message={msg} />
        ) : (
          <AssistantMessage key={msg.id} message={msg} />
        ),
      )}
      {streaming && <StreamingAssistantMessage {...streaming} />}
    </>
  )
}

// ---------------------------------------------------------------------------
// UserMessage
// ---------------------------------------------------------------------------

function UserMessage({ message }: { message: Message }) {
  const text = message.parts
    .filter((p) => p.type === 'text')
    .map((p) => (p as { text: string }).text)
    .join('\n')

  return (
    <div className="mb-6 flex w-full max-w-3xl gap-3 justify-end">
      <div className="rounded-2xl rounded-br-md bg-primary/90 backdrop-blur-sm px-4 py-2.5 text-sm text-primary-foreground whitespace-pre-wrap shadow-sm">
        {text}
      </div>
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/15 backdrop-blur-sm">
        <UserIcon className="size-3.5 text-primary" />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AssistantMessage (completed)
// ---------------------------------------------------------------------------

function AssistantMessage({ message }: { message: Message }) {
  return (
    <div className="mb-6 flex w-full max-w-3xl gap-3 items-start">
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-background/60 backdrop-blur-sm">
        <BotIcon className="size-3.5 text-muted-foreground" />
      </div>
      <div className="max-w-full min-w-0 flex-1 rounded-2xl rounded-tl-md bg-background/70 backdrop-blur-xl px-4 py-3 text-sm shadow-sm">
        {message.parts.map((part, i) => {
          switch (part.type) {
            case 'text':
              return <MarkdownContent key={i} content={part.text} />
            case 'tool':
              return (
                <ToolCallBlock
                  key={i}
                  toolName={part.tool_name}
                  args={part.tool_input}
                  result={part.tool_output}
                  isError={part.tool_status === 'error'}
                  isRunning={false}
                />
              )
            case 'context':
              return null
          }
        })}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// StreamingAssistantMessage (in-flight)
// ---------------------------------------------------------------------------

function StreamingAssistantMessage({
  content,
  toolCalls,
  reasoning,
  iteration,
}: {
  content: string
  toolCalls: StreamToolCall[]
  reasoning: string
  iteration: number
}) {
  return (
    <div className="mb-6 flex w-full max-w-3xl gap-3 items-start">
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-background/60 backdrop-blur-sm">
        <BotIcon className="size-3.5 text-muted-foreground" />
      </div>
      <div className="max-w-full min-w-0 flex-1 rounded-2xl rounded-tl-md bg-background/70 backdrop-blur-xl px-4 py-3 text-sm shadow-sm">
        {iteration > 1 && (
          <div className="mb-2">
            <span className="inline-flex items-center rounded-md bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
              Iteration {iteration}
            </span>
          </div>
        )}

        <ReasoningBlock reasoning={reasoning} isRunning />

        {toolCalls.map((tc, i) => {
          let args: Record<string, unknown> = {}
          try {
            args = JSON.parse(tc.arguments) as Record<string, unknown>
          } catch {
            if (tc.arguments) args = { raw: tc.arguments }
          }
          return (
            <ToolCallBlock
              key={i}
              toolName={tc.name}
              args={args}
              result={tc.result}
              isRunning={!tc.result}
            />
          )
        })}

        <MarkdownContent content={content} isStreaming />
      </div>
    </div>
  )
}
