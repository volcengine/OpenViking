import { memo } from 'react'
import { BotIcon, FileIcon, ImageIcon, UserIcon } from 'lucide-react'

import type { Message } from '#/routes/sessions/-types/message'
import type { StreamToolCall } from '#/routes/sessions/-types/chat'
import { MarkdownContent, ReasoningBlock, ToolCallBlock } from './message-parts'

// ---------------------------------------------------------------------------
// Attachment tag parsing
// ---------------------------------------------------------------------------

const ATTACHMENT_RE = /^\[uploaded_file:\s*(.+?),\s*temp_file_id:\s*(.+?)\]\n?/

function parseAttachment(text: string): {
  fileName: string
  tempFileId: string
  rest: string
} | null {
  const match = text.match(ATTACHMENT_RE)
  if (!match) return null
  return { fileName: match[1], tempFileId: match[2], rest: text.slice(match[0].length) }
}

function isImageFile(name: string): boolean {
  return /\.(jpg|jpeg|png|gif|webp|svg|bmp|ico)$/i.test(name)
}

// ---------------------------------------------------------------------------
// MessageList
// ---------------------------------------------------------------------------

interface MessageListProps {
  messages: Message[]
  attachmentPreviews?: Map<string, string>
  streaming?: {
    content: string
    toolCalls: StreamToolCall[]
    reasoning: string
    iteration: number
  }
}

export function MessageList({ messages, attachmentPreviews, streaming }: MessageListProps) {
  return (
    <>
      {messages.map((msg) =>
        msg.role === 'user' ? (
          <UserMessage key={msg.id} message={msg} attachmentPreviews={attachmentPreviews} />
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

const UserMessage = memo(function UserMessage({
  message,
  attachmentPreviews,
}: {
  message: Message
  attachmentPreviews?: Map<string, string>
}) {
  const rawText = message.parts
    .filter((p) => p.type === 'text')
    .map((p) => (p as { text: string }).text)
    .join('\n')

  const parsed = parseAttachment(rawText)
  const text = parsed ? parsed.rest : rawText
  const previewUrl = parsed ? attachmentPreviews?.get(parsed.tempFileId) : undefined

  return (
    <div className="mb-6 flex w-full max-w-3xl gap-3 justify-end">
      <div className="max-w-[75%] space-y-2">
        {/* Attachment card */}
        {parsed && (
          <div className="overflow-hidden rounded-2xl rounded-br-md border border-primary/20 bg-primary/90 shadow-sm">
            {previewUrl && isImageFile(parsed.fileName) ? (
              <img
                src={previewUrl}
                alt={parsed.fileName}
                className="max-h-64 w-full object-cover"
              />
            ) : null}
            <div className="flex items-center gap-2 px-3 py-2 text-xs text-primary-foreground/80">
              {isImageFile(parsed.fileName) ? (
                <ImageIcon className="size-3.5 shrink-0" />
              ) : (
                <FileIcon className="size-3.5 shrink-0" />
              )}
              <span className="min-w-0 flex-1 truncate">{parsed.fileName}</span>
            </div>
          </div>
        )}
        {/* Text bubble */}
        {text && (
          <div className="rounded-2xl rounded-br-md bg-primary/90 backdrop-blur-sm px-4 py-2.5 text-sm text-primary-foreground whitespace-pre-wrap shadow-sm">
            {text}
          </div>
        )}
      </div>
      <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/15 backdrop-blur-sm">
        <UserIcon className="size-3.5 text-primary" />
      </div>
    </div>
  )
})

// ---------------------------------------------------------------------------
// AssistantMessage (completed)
// ---------------------------------------------------------------------------

const AssistantMessage = memo(function AssistantMessage({ message }: { message: Message }) {
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
})

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
