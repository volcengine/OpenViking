import { CompassIcon } from 'lucide-react'
import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useQueryClient } from '@tanstack/react-query'
import { OpenVikingProvider } from '@openviking/react/client'
import { Console } from '@openviking/react/modules/console'
import '@openviking/react/modules/console/style.css'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { MemoryImpact } from './memory-impact'
import { createThreadRequest } from './thread-request'
import styles from './thread.module.css'
import { copyTextToClipboard } from '#/shared/lib/clipboard'
import { sharedMessages } from '../lib/shared-messages'
import { getSessionTitle, useSessionTitles } from '../lib/use-session-titles'
import { useSession, useSessionMessages } from '../lib/use-sessions'

const PixelBlast = lazy(() => import('#/shared/ui/pixel-blast'))
const PRODUCT_NAME = 'OpenViking'

interface ThreadProps {
  sessionId: string
  draft?: boolean
  onPersisted?: () => void
}

export function Thread(props: ThreadProps) {
  const { identityScopeKey } = useAppConnection()
  return (
    <SessionThread key={`${identityScopeKey}:${props.sessionId}`} {...props} />
  )
}

function SessionThread({ sessionId, draft = false, onPersisted }: ThreadProps) {
  const { t, i18n } = useTranslation('sessions')
  const service = useStudioService()
  const canChat = service.ready && service.provider === 'opensource'
  const { identityScopeKey } = useAppConnection()
  const { getTitle, setTitle } = useSessionTitles(identityScopeKey)
  const title = getTitle(sessionId)
  const [historyId] = useState(draft ? undefined : sessionId)
  const [currentId, setCurrentId] = useState(historyId)
  const queryClient = useQueryClient()
  const request = useMemo(
    () =>
      createThreadRequest({
        sessionId,
        hasTitle: () => {
          // Read at save completion; restored titles must not replace the stream owner.
          const current = getSessionTitle(identityScopeKey, sessionId)
          return (
            current !== sessionId &&
            current !== i18n.t('sessions:threadList.newSession')
          )
        },
        onMessageSaved: (text) => {
          if (text) setTitle(sessionId, text.slice(0, 20))
          void queryClient.invalidateQueries({ queryKey: ['sessions'] })
        },
      }),
    [sessionId, identityScopeKey, i18n, setTitle, queryClient],
  )
  const { data: session } = useSession(currentId)
  const {
    data: historyMessages,
    isPending,
    error,
  } = useSessionMessages(
    service.provider === 'volcengine' ? historyId : undefined,
  )
  const readOnlyMessages = useMemo(
    () => sharedMessages(historyMessages ?? []),
    [historyMessages],
  )
  return (
    <OpenVikingProvider baseURL="" fetch={request}>
      <Console
        className={styles.console}
        modes={['conversation']}
        locale={i18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
        scopeKey={identityScopeKey}
        conversation={{
          messages: canChat ? undefined : readOnlyMessages,
          composer: canChat ? undefined : false,
          isAvailable: canChat,
          onCopy: (text) => void copyTextToClipboard(text),
          sessions: {
            items: [],
            currentId,
            onNew: () => {},
            onSelect: (id) => {
              setCurrentId(id)
              if (draft) onPersisted?.()
            },
          },
          labels: {
            thinking: t('chat.generating'),
            reasoning: t('chat.reasoning'),
            copy: t('chat.copy'),
            placeholder: t('chat.placeholder'),
            send: t('chat.send'),
            stop: t('chat.cancel'),
          },
          header: (
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
          ),
          emptyState: <ThreadEmpty canChat={canChat} />,
          messageList:
            service.provider === 'volcengine' && historyId ? (
              isPending ? (
                <div
                  role="status"
                  className="py-6 text-center text-sm text-muted-foreground"
                >
                  {t('threadList.loading')}
                </div>
              ) : error ? (
                <div
                  role="alert"
                  className="py-6 text-center text-sm text-destructive"
                >
                  {t('chat.historyLoadFailed', { error: error.message })}
                </div>
              ) : undefined
            ) : undefined,
        }}
      />
    </OpenVikingProvider>
  )
}

function ThreadEmpty({ canChat }: { canChat: boolean }) {
  const { t } = useTranslation('sessions')
  const [showBackground, setShowBackground] = useState(false)
  useEffect(() => {
    const id = globalThis.setTimeout(() => setShowBackground(true), 200)
    return () => clearTimeout(id)
  }, [])

  return (
    <div className="relative flex grow flex-col items-center justify-center gap-3">
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
      <div className="relative flex size-14 items-center justify-center rounded-2xl bg-gradient-to-br from-primary/15 to-primary/5 ring-1 ring-primary/10">
        <CompassIcon className="size-7 text-primary/70" />
      </div>
      <div className="relative text-center">
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
