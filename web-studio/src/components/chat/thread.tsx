import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { CompassIcon } from 'lucide-react'

import { useChat } from '#/routes/sessions/-hooks/use-chat'
import { useSessionMessages } from '#/routes/sessions/-hooks/use-sessions'
import { useSessionTitles } from '#/routes/sessions/-hooks/use-session-titles'
import { useFileAttachment } from '#/routes/sessions/-hooks/use-file-attachment'
import { MessageList } from './message-list'
import { Composer } from './composer'

const PixelBlast = lazy(() => import('#/components/ui/pixel-blast'))

interface ThreadProps {
  sessionId: string
}

export function Thread({ sessionId }: ThreadProps) {
  const { getTitle } = useSessionTitles()
  const title = getTitle(sessionId)

  const { data: historyMessages } = useSessionMessages(sessionId)

  const chat = useChat({
    sessionId,
    initialMessages: historyMessages,
    persistMessages: true,
  })

  const isStreaming = chat.status === 'streaming'

  // ---- File attachment ----
  const { attachment, attach, clear: clearAttachment } = useFileAttachment()
  const attachmentPreviewsRef = useRef<Map<string, string>>(new Map())

  const handleSend = useCallback(
    (message: string) => {
      let text = message
      if (attachment.phase === 'ready' && attachment.tempFileId) {
        const prefix = `[uploaded_file: ${attachment.fileName}, temp_file_id: ${attachment.tempFileId}]`
        text = text ? `${prefix}\n${text}` : prefix
        // Store preview URL for this temp_file_id (survives attachment clear)
        if (attachment.previewUrl) {
          attachmentPreviewsRef.current.set(attachment.tempFileId, attachment.previewUrl)
        }
        clearAttachment()
      }
      if (text.trim()) chat.send(text)
    },
    [attachment, clearAttachment, chat],
  )

  // ---- Auto-scroll ----
  const scrollRef = useRef<HTMLDivElement>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const isNearBottomRef = useRef(true)
  const scrollRafRef = useRef(0)

  const handleScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    isNearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100
  }, [])

  useEffect(() => {
    if (!isNearBottomRef.current) return
    cancelAnimationFrame(scrollRafRef.current)
    scrollRafRef.current = requestAnimationFrame(() => {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    })
  }, [chat.messages.length, chat.streamingContent, chat.streamingToolCalls, chat.streamingReasoning])

  const [showBackground, setShowBackground] = useState(false)

  useEffect(() => {
    const id = 'requestIdleCallback' in window
      ? window.requestIdleCallback(() => setShowBackground(true))
      : window.setTimeout(() => setShowBackground(true), 200)
    return () => {
      if ('requestIdleCallback' in window) window.cancelIdleCallback(id as number)
      else clearTimeout(id)
    }
  }, [])

  const isEmpty = chat.messages.length === 0 && !isStreaming

  return (
    <div className="relative flex h-full flex-col">
      {/* PixelBlast background — deferred until idle */}
      {showBackground && (
        <div className="pointer-events-none absolute inset-0 z-0 opacity-40">
          <Suspense fallback={null}>
            <PixelBlast
              color="#008bad"
              pixelSize={1}
              edgeFade={0.2}
              speed={1.55}
              enableRipples={false}
            />
          </Suspense>
        </div>
      )}

      {title && (
        <div className="relative z-10 flex h-12 items-center border-b border-border/50 bg-background/95 px-6">
          <h2 className="text-sm font-medium truncate text-foreground">{title}</h2>
        </div>
      )}

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="relative z-10 flex flex-1 flex-col items-center overflow-y-auto px-4 pt-12 pb-24"
      >
        {isEmpty ? (
          <ThreadEmpty />
        ) : (
          <MessageList
            messages={chat.messages}
            attachmentPreviews={attachmentPreviewsRef.current}
            streaming={
              isStreaming
                ? {
                    content: chat.streamingContent,
                    toolCalls: chat.streamingToolCalls,
                    reasoning: chat.streamingReasoning,
                    iteration: chat.iteration,
                  }
                : undefined
            }
          />
        )}
        <div ref={bottomRef} />
      </div>

      <div className="relative z-10">
        <Composer
          onSend={handleSend}
          onCancel={chat.abort}
          isStreaming={isStreaming}
          attachment={attachment}
          onAttach={attach}
          onClearAttachment={clearAttachment}
        />
      </div>
    </div>
  )
}

function ThreadEmpty() {
  return (
    <div className="flex grow flex-col items-center justify-center gap-3">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-gradient-to-br from-primary/15 to-primary/5 ring-1 ring-primary/10">
        <CompassIcon className="size-7 text-primary/70" />
      </div>
      <div className="text-center">
        <h3 className="text-base font-medium text-foreground">OpenViking</h3>
        <p className="mt-1 text-sm text-muted-foreground">
          探索你的知识库，开始一段对话。
        </p>
      </div>
    </div>
  )
}
