import { memo, useCallback, useState } from 'react'
import { CheckIcon, CopyIcon, UserIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { resolvePublicAsset } from '#/lib/public-path'
import type { Message, MessagePart, ToolPart } from '#/lib/sessions/types/message'
import { MarkdownContent, ReasoningBlock, ToolCallBlock } from './message-parts'

const OPENVIKING_ICON_SRC = resolvePublicAsset('favicon-32.png')

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
  const parts = Array.isArray(message.parts) ? message.parts : []
  return parts
    .filter((p) => p.type === 'text')
    .map((p) => (p as { text: string }).text)
    .join('\n')
}

function stripPlaygroundContextSuffix(text: string): string {
  return text
    .replace(/\n\n当前选中的上下文资源：\s*viking:\/\/[\s\S]*$/u, '')
    .replace(
      /\n\n当前用户在 Playground 中选中的上下文资源是：\s*viking:\/\/[\s\S]*$/u,
      '',
    )
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

function BotAvatar({ compact }: { compact?: boolean }) {
  const sizeClass = compact ? 'size-6' : 'size-7'
  return (
    <div className={`flex ${sizeClass} shrink-0 items-center justify-center rounded-full ring-1 ring-border/20 overflow-hidden`}>
      <img src={OPENVIKING_ICON_SRC} alt="OpenViking" className={sizeClass} />
    </div>
  )
}

// ---------------------------------------------------------------------------
// MessageList
// ---------------------------------------------------------------------------

interface MessageListProps {
  layout?: 'default' | 'expanded'
  messages: Message[]
  streaming?: {
    parts?: MessagePart[]
    iteration: number
  }
  onResourceClick?: (uri: string) => void
}

export function MessageList({
  layout = 'default',
  messages,
  onResourceClick,
  streaming,
}: MessageListProps) {
  const isExpanded = layout === 'expanded'
  const safeMessages = Array.isArray(messages) ? messages : []
  return (
    <>
      {safeMessages.map((msg, idx) => {
        const prev = idx > 0 ? safeMessages[idx - 1] : null
        const sameRole = prev?.role === msg.role
        return msg.role === 'user' ? (
          <UserMessage
            key={msg.id}
            message={msg}
            compact={sameRole}
            expanded={isExpanded}
          />
        ) : (
          <AssistantMessage
            key={msg.id}
            message={msg}
            compact={sameRole}
            expanded={isExpanded}
            onResourceClick={onResourceClick}
          />
        )
      })}
      {streaming && (
        <StreamingAssistantMessage
          {...streaming}
          expanded={isExpanded}
          onResourceClick={onResourceClick}
        />
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// UserMessage
// ---------------------------------------------------------------------------

const UserMessage = memo(function UserMessage({
  expanded,
  message,
  compact,
}: {
  expanded?: boolean
  message: Message
  compact?: boolean
}) {
  const text = stripPlaygroundContextSuffix(getTextFromParts(message))

  return (
    <div
      className={`${expanded ? 'w-full' : 'w-full max-w-3xl'} group/msg flex gap-2 justify-end ${compact ? 'mb-1.5' : 'mb-5'}`}
    >
      <div className="flex items-end gap-1.5 self-end opacity-0 transition-opacity group-hover/msg:opacity-100">
        <span className="text-[10px] text-muted-foreground/40 opacity-0 transition-opacity group-hover/msg:opacity-100 select-none">
          {formatRelativeTime(message.created_at)}
        </span>
        <CopyButton text={text} />
      </div>
      <div className={expanded ? 'max-w-[88%] space-y-1.5' : 'max-w-[75%] space-y-1.5'}>
        {text && (
          <div className="whitespace-pre-wrap rounded-2xl rounded-tr-sm border border-border/70 bg-muted/70 px-4 py-2.5 text-sm leading-6 text-foreground shadow-sm">
            {text}
          </div>
        )}
      </div>
      {!compact && (
        <div className="flex size-6 shrink-0 items-center justify-center rounded-full border border-border/70 bg-muted/60">
          <UserIcon className="size-3.5 text-muted-foreground" />
        </div>
      )}
      {compact && <div className="w-6 shrink-0" />}
    </div>
  )
})

// ---------------------------------------------------------------------------
// AssistantMessage (completed)
// ---------------------------------------------------------------------------

const AssistantMessage = memo(function AssistantMessage({
  expanded,
  message,
  compact,
  onResourceClick,
}: {
  expanded?: boolean
  message: Message
  compact?: boolean
  onResourceClick?: (uri: string) => void
}) {
  const textContent = getTextFromParts(message)

  return (
    <div
      className={`${expanded ? 'w-full' : 'w-full max-w-3xl'} group/msg flex gap-2 items-start ${compact ? 'mb-1.5' : 'mb-5'}`}
    >
      {!compact ? <BotAvatar compact={expanded} /> : <div className="w-6 shrink-0" />}
      <div className="relative max-w-full min-w-0 flex-1 rounded-2xl rounded-tl-sm bg-background/95 px-4 py-3 text-sm shadow-sm ring-1 ring-border/30">
        {(Array.isArray(message.parts) ? message.parts : []).map((part, i) => {
          switch (part.type) {
            case 'text':
              return <MarkdownContent key={i} content={part.text} />
            case 'reasoning':
              return (
                <ReasoningBlock
                  key={i}
                  reasoning={part.reasoning}
                  isRunning={false}
                />
              )
            case 'tool':
              return (
                <ToolCallBlock
                  key={i}
                  toolName={part.tool_name}
                  args={part.tool_input}
                  result={part.tool_output}
                  isError={part.tool_status === 'error'}
                  isRunning={false}
                  onResourceClick={onResourceClick}
                />
              )
            case 'context':
              return null
          }
        })}
        <div className="absolute right-2 top-2 flex items-center gap-1.5 rounded-lg bg-background/85 px-1.5 py-1 opacity-0 shadow-sm ring-1 ring-border/40 backdrop-blur transition-opacity group-hover/msg:opacity-100">
          <CopyButton text={textContent} />
          <span className="text-[10px] text-muted-foreground/60 select-none">
            {formatRelativeTime(message.created_at)}
          </span>
        </div>
      </div>
    </div>
  )
})

// ---------------------------------------------------------------------------
// StreamingAssistantMessage (in-flight)
// ---------------------------------------------------------------------------

function StreamingAssistantMessage({
  expanded,
  parts = [],
  iteration,
  onResourceClick,
}: {
  expanded?: boolean
  parts?: MessagePart[]
  iteration: number
  onResourceClick?: (uri: string) => void
}) {
  const { t } = useTranslation('sessions')
  const safeParts = Array.isArray(parts) ? parts : []
  const hasContent = safeParts.length > 0

  return (
    <div className={`${expanded ? 'w-full' : 'w-full max-w-3xl'} mb-5 flex gap-2 items-start`}>
      <BotAvatar compact={expanded} />
      <div className="max-w-full min-w-0 flex-1 rounded-2xl rounded-tl-sm bg-background/95 px-4 py-3 text-sm shadow-sm ring-1 ring-border/30">
        {iteration > 1 && (
          <div className="mb-2">
            <span className="inline-flex items-center rounded-full bg-primary/10 px-2.5 py-0.5 text-[11px] font-medium text-primary">
              {t('chat.iteration', { count: iteration })}
            </span>
          </div>
        )}

        {safeParts.map((part, i) => renderStreamingPart(part, i, onResourceClick))}

        {!hasContent ? (
          <TypingIndicator />
        ) : null}
      </div>
    </div>
  )
}

function renderStreamingPart(
  part: MessagePart,
  index: number,
  onResourceClick?: (uri: string) => void,
) {
  switch (part.type) {
    case 'reasoning':
      return (
        <ReasoningBlock
          key={index}
          reasoning={part.reasoning}
          isRunning={part.is_running ?? true}
        />
      )
    case 'tool':
      return (
        <StreamingToolPart
          key={index}
          part={part}
          onResourceClick={onResourceClick}
        />
      )
    case 'text':
      return <MarkdownContent key={index} content={part.text} isStreaming />
    case 'context':
      return null
  }
}

function StreamingToolPart({
  part,
  onResourceClick,
}: {
  part: ToolPart
  onResourceClick?: (uri: string) => void
}) {
  return (
    <ToolCallBlock
      toolName={part.tool_name}
      args={part.tool_input}
      result={part.tool_output}
      isError={part.tool_status === 'error'}
      isRunning={part.tool_status === 'running' || part.tool_status === 'pending'}
      onResourceClick={onResourceClick}
    />
  )
}
