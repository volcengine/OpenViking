import { memo, useCallback, useState } from 'react'
import { CheckIcon, CopyIcon, UserIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { resolvePublicAsset } from '#/lib/public-path'
import type { StreamToolCall } from '#/lib/sessions/types/chat'
import type { Message } from '#/lib/sessions/types/message'
import { MarkdownContent, ReasoningBlock, ToolCallBlock } from './message-parts'

const OPENVIKING_ICON_SRC = resolvePublicAsset('openviking-icon.png')

// ---------------------------------------------------------------------------
// CopyButton
// ---------------------------------------------------------------------------

function CopyButton({ text }: { text: string }) {
  const { t } = useTranslation('sessions')
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }, [text])

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="inline-flex size-6 items-center justify-center rounded-md text-muted-foreground/50 opacity-0 transition-all group-hover/msg:opacity-100 hover:bg-accent hover:text-accent-foreground"
      title={t('chat.copy')}
    >
      {copied ? (
        <CheckIcon className="size-3" />
      ) : (
        <CopyIcon className="size-3" />
      )}
    </button>
  )
}

/** Extract all text content from a message's parts. */
function getTextFromParts(message: Message): string {
  return message.parts
    .filter((p) => p.type === 'text')
    .map((p) => (p as { text: string }).text)
    .join('\n')
}

/** Format relative time */
function formatRelativeTime(iso: string): string {
  const now = Date.now()
  const then = new Date(iso).getTime()
  const diff = Math.max(0, now - then)
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  return `${days} 天前`
}

// ---------------------------------------------------------------------------
// TypingIndicator
// ---------------------------------------------------------------------------

function TypingIndicator() {
  return (
    <div className="flex items-center gap-1 py-1">
      <span className="size-1.5 rounded-full bg-muted-foreground/40 animate-bounce [animation-delay:0ms]" />
      <span className="size-1.5 rounded-full bg-muted-foreground/40 animate-bounce [animation-delay:150ms]" />
      <span className="size-1.5 rounded-full bg-muted-foreground/40 animate-bounce [animation-delay:300ms]" />
    </div>
  )
}

// ---------------------------------------------------------------------------
// BotAvatar — product brand avatar
// ---------------------------------------------------------------------------

function BotAvatar() {
  return (
    <div className="flex size-7 shrink-0 items-center justify-center rounded-full ring-1 ring-border/20 overflow-hidden">
      <img src={OPENVIKING_ICON_SRC} alt="OpenViking" className="size-7" />
    </div>
  )
}

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
      {messages.map((msg, idx) => {
        const prev = idx > 0 ? messages[idx - 1] : null
        const sameRole = prev?.role === msg.role
        return msg.role === 'user' ? (
          <UserMessage key={msg.id} message={msg} compact={sameRole} />
        ) : (
          <AssistantMessage key={msg.id} message={msg} compact={sameRole} />
        )
      })}
      {streaming && <StreamingAssistantMessage {...streaming} />}
    </>
  )
}

// ---------------------------------------------------------------------------
// UserMessage
// ---------------------------------------------------------------------------

const UserMessage = memo(function UserMessage({
  message,
  compact,
}: {
  message: Message
  compact?: boolean
}) {
  const text = getTextFromParts(message)

  return (
    <div
      className={`group/msg flex w-full max-w-3xl gap-3 justify-end ${compact ? 'mb-1.5' : 'mb-5'}`}
    >
      <div className="flex items-end gap-1.5 self-end">
        <span className="text-[10px] text-muted-foreground/40 opacity-0 transition-opacity group-hover/msg:opacity-100 select-none">
          {formatRelativeTime(message.created_at)}
        </span>
        <CopyButton text={text} />
      </div>
      <div className="max-w-[75%] space-y-1.5">
        {text && (
          <div className="rounded-2xl rounded-tr-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground whitespace-pre-wrap shadow-sm">
            {text}
          </div>
        )}
      </div>
      {!compact && (
        <div className="flex size-7 shrink-0 items-center justify-center rounded-full bg-primary/10">
          <UserIcon className="size-3.5 text-primary" />
        </div>
      )}
      {compact && <div className="w-7 shrink-0" />}
    </div>
  )
})

// ---------------------------------------------------------------------------
// AssistantMessage (completed)
// ---------------------------------------------------------------------------

const AssistantMessage = memo(function AssistantMessage({
  message,
  compact,
}: {
  message: Message
  compact?: boolean
}) {
  const textContent = getTextFromParts(message)

  return (
    <div
      className={`group/msg flex w-full max-w-3xl gap-3 items-start ${compact ? 'mb-1.5' : 'mb-5'}`}
    >
      {!compact ? <BotAvatar /> : <div className="w-7 shrink-0" />}
      <div className="max-w-full min-w-0 flex-1 rounded-2xl rounded-tl-sm bg-background/95 px-4 py-3 text-sm shadow-sm ring-1 ring-border/30">
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
      <div className="flex items-end gap-1.5 self-end">
        <CopyButton text={textContent} />
        <span className="text-[10px] text-muted-foreground/40 opacity-0 transition-opacity group-hover/msg:opacity-100 select-none">
          {formatRelativeTime(message.created_at)}
        </span>
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
  const { t } = useTranslation('sessions')
  const hasContent = content || toolCalls.length > 0 || reasoning

  return (
    <div className="mb-5 flex w-full max-w-3xl gap-3 items-start">
      <BotAvatar />
      <div className="max-w-full min-w-0 flex-1 rounded-2xl rounded-tl-sm bg-background/95 px-4 py-3 text-sm shadow-sm ring-1 ring-border/30">
        {iteration > 1 && (
          <div className="mb-2">
            <span className="inline-flex items-center rounded-full bg-primary/10 px-2.5 py-0.5 text-[11px] font-medium text-primary">
              {t('chat.iteration', { count: iteration })}
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

        {content ? (
          <MarkdownContent content={content} isStreaming />
        ) : !hasContent ? (
          <TypingIndicator />
        ) : null}
      </div>
    </div>
  )
}
