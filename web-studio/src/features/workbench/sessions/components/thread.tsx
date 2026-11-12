import { CompassIcon } from 'lucide-react'
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { MemoryImpact } from '#/features/workbench/sessions/components/memory-impact'
import { useVikingBotChat } from '@openviking/react/modules/viking-bot'
import { OpenVikingProvider } from '@openviking/react/client'
import { VikingBot } from '@openviking/react/components/viking-bot'
import type {
  VikingBotMessage,
  VikingBotDraft,
} from '@openviking/react/components/viking-bot'
import '@openviking/react/components/viking-bot/style.css'
import { copyTextToClipboard } from '#/shared/lib/clipboard'
import { sharedMessages } from '#/features/workbench/sessions/lib/shared-messages'
import { requestStudioModule } from '#/shared/api/module-request'
import studioI18n from '#/i18n/core'
import { useSessionTitles } from '#/features/workbench/sessions/lib/use-session-titles'
import {
  useCreateSession,
  useSession,
  useSessionMessages,
} from '#/features/workbench/sessions/lib/use-sessions'

const PixelBlast = lazy(() => import('#/shared/ui/pixel-blast'))
const PRODUCT_NAME = 'OpenViking'
const emptyMessages: VikingBotMessage[] = []

interface ThreadProps {
  sessionId: string
  draft?: boolean
  onPersisted?: () => void
}

export function Thread(props: ThreadProps) {
  const { identityScopeKey } = useAppConnection()
  return (
    <OpenVikingProvider baseURL="" fetch={requestStudioModule}>
      <SessionThread
        key={`${identityScopeKey}:${props.sessionId}`}
        {...props}
      />
    </OpenVikingProvider>
  )
}

function SessionThread({ sessionId, draft = false, onPersisted }: ThreadProps) {
  const { t } = useTranslation('sessions')
  const service = useStudioService()
  const canChat = service.ready && service.provider === 'opensource'
  const { identityScopeKey } = useAppConnection()
  const { getTitle, setTitle } = useSessionTitles(identityScopeKey)
  const title = getTitle(sessionId)

  const [historyId] = useState(draft ? undefined : sessionId)
  const persisted = useRef(!draft)
  const creating = useRef(false)
  const mounted = useRef(false)
  const [input, setInput] = useState('')
  const submission = useRef(0)
  const [isCreating, setIsCreating] = useState(false)
  const [creationError, setCreationError] = useState<string>()
  const createSession = useCreateSession()
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const { data: session } = useSession(historyId)
  const {
    data: historyMessages,
    isPending: remoteHistoryLoading,
    error: remoteHistoryError,
  } = useSessionMessages(
    service.provider === 'volcengine' ? historyId : undefined,
  )
  const readOnlyMessages = useMemo(
    () => sharedMessages(historyMessages ?? []),
    [historyMessages],
  )

  const chat = useVikingBotChat({
    scopeKey: identityScopeKey,
    sessionId,
    enabled: canChat,
    initialMessages:
      service.provider === 'volcengine'
        ? readOnlyMessages
        : historyId
          ? undefined
          : emptyMessages,
    locale: studioI18n.language.startsWith('zh') ? 'zh-CN' : 'en-US',
  })
  const historyLoading =
    service.provider === 'volcengine'
      ? remoteHistoryLoading
      : chat.status === 'loading'
  const historyError =
    service.provider === 'volcengine' ? remoteHistoryError : undefined

  const isStreaming = chat.status === 'streaming'

  const handleSend = useCallback(
    async (message: string) => {
      if (!canChat || !message.trim() || creating.current) return false
      if (!persisted.current) {
        creating.current = true
        setIsCreating(true)
        setCreationError(undefined)
        try {
          await createSession.mutateAsync(sessionId)
          onPersisted?.()
          if (!mounted.current) return false
          persisted.current = true
        } catch (error) {
          if (mounted.current)
            setCreationError(
              error instanceof Error ? error.message : String(error),
            )
          return false
        } finally {
          creating.current = false
          if (mounted.current) setIsCreating(false)
        }
      }
      const firstExchange = !chat.messages.some((item) => item.role === 'user')
      const accepted = await chat.send(message)
      if (accepted && firstExchange && mounted.current)
        setTitle(sessionId, message.slice(0, 20))
      return accepted
    },
    [canChat, chat, createSession, sessionId, onPersisted, setTitle],
  )

  const submit = async (inputDraft: VikingBotDraft) => {
    const token = ++submission.current
    setInput((current) =>
      current.trim() === inputDraft.text.trim() ? '' : current,
    )
    const accepted = await handleSend(inputDraft.text)
    if (!accepted && mounted.current && token === submission.current)
      setInput((current) => (current === '' ? inputDraft.text : current))
    return accepted
  }

  const [showBackground, setShowBackground] = useState(false)

  useEffect(() => {
    const id =
      'requestIdleCallback' in window
        ? window.requestIdleCallback(() => setShowBackground(true))
        : globalThis.setTimeout(() => setShowBackground(true), 200)
    return () => {
      if ('requestIdleCallback' in window)
        window.cancelIdleCallback(id as number)
      else clearTimeout(id)
    }
  }, [])

  const isEmpty = chat.messages.length === 0 && !isStreaming

  return (
    <div className="relative flex h-full min-h-0 flex-1 flex-col">
      {/* PixelBlast background — deferred until idle */}
      {showBackground && isEmpty && (
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

      <VikingBot
        className="relative z-10"
        locale={studioI18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
        messages={chat.messages}
        streaming={chat.streaming}
        listKey={`${identityScopeKey}:${sessionId}`}
        draft={canChat ? input : undefined}
        composer={canChat ? undefined : false}
        onDraftChange={setInput}
        onSend={submit}
        isSending={isStreaming}
        canSendWhileStreaming={chat.canSteer && isStreaming}
        isSubmitting={isCreating || chat.isSubmitting}
        isAvailable={canChat && !historyLoading}
        onCancel={
          chat.canInterrupt && isStreaming ? () => void chat.abort() : undefined
        }
        onCopy={(text) => void copyTextToClipboard(text)}
        labels={{
          thinking: t('chat.generating'),
          reasoning: t('chat.reasoning'),
          copy: t('chat.copy'),
          placeholder: t('chat.placeholder'),
          send: t('chat.send'),
          stop: t('chat.cancel'),
        }}
        header={
          <div className="flex h-12 items-center justify-between gap-4 border-b border-border/50 bg-background/95 px-6">
            <h2 className="truncate text-sm font-medium text-foreground">
              {draft && title === sessionId
                ? t('threadList.newSession')
                : title || sessionId}
            </h2>
            {service.provider === 'opensource' ? (
              <MemoryImpact session={session} />
            ) : null}
          </div>
        }
        emptyState={<ThreadEmpty canChat={canChat} />}
        messageList={
          historyId && historyLoading ? (
            <div
              role="status"
              className="py-6 text-center text-sm text-muted-foreground"
            >
              {t('threadList.loading')}
            </div>
          ) : historyError ? (
            <div
              role="alert"
              className="py-6 text-center text-sm text-destructive"
            >
              {t('chat.historyLoadFailed', { error: historyError.message })}
            </div>
          ) : undefined
        }
        footer={
          creationError || chat.error || chat.persistenceError ? (
            <div role="alert" className="px-4 py-2 text-sm text-destructive">
              {creationError || chat.error
                ? t(
                    historyId && !chat.messages.length && !creationError
                      ? 'chat.historyLoadFailed'
                      : 'chat.sendFailed',
                    { error: creationError || chat.error },
                  )
                : t('chat.persistFailed', {
                    error: chat.persistenceError?.message,
                  })}
            </div>
          ) : undefined
        }
      />
    </div>
  )
}

function ThreadEmpty({ canChat }: { canChat: boolean }) {
  const { t } = useTranslation('sessions')

  return (
    <div className="flex grow flex-col items-center justify-center gap-3">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-gradient-to-br from-primary/15 to-primary/5 ring-1 ring-primary/10">
        <CompassIcon className="size-7 text-primary/70" />
      </div>
      <div className="text-center">
        <h3 className="text-base font-medium text-foreground">
          {PRODUCT_NAME}
        </h3>
        <p className="mt-1 text-sm text-muted-foreground">
          {t(canChat ? 'chat.emptyDescription' : 'chat.emptyReadDescription')}
        </p>
      </div>
    </div>
  )
}
