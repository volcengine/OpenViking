import {
  OpenVikingProvider,
  OV_PATH_BUILDERS,
  OV_PATHS,
} from '@openviking/react/client'
import { Console } from '@openviking/react/modules/console'
import '@openviking/react/modules/console/style.css'
import { useQueryClient } from '@tanstack/react-query'
import { CompassIcon } from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { MemoryImpact } from '#/features/workbench/sessions/components/memory-impact'
import { sharedMessages } from '#/features/workbench/sessions/lib/shared-messages'
import { useDefaultConversationTitles } from '#/features/workbench/sessions/lib/use-default-conversation-titles'
import { useSessionTitles } from '#/features/workbench/sessions/lib/use-session-titles'
import {
  useSession,
  useSessionMessages,
} from '#/features/workbench/sessions/lib/use-sessions'
import { requestStudioModule } from '#/shared/api/module-request'
import { copyTextToClipboard } from '#/shared/lib/clipboard'
import styles from './thread.module.css'

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
    <SessionThread
      key={`${identityScopeKey}:${props.sessionId}`}
      scope={identityScopeKey}
      {...props}
    />
  )
}

function SessionThread({
  sessionId,
  draft = false,
  onPersisted,
  scope,
}: ThreadProps & { scope: string }) {
  const { t, i18n } = useTranslation('sessions')
  const { provider, ready } = useStudioService()
  const canChat = ready && provider === 'opensource'
  const queryClient = useQueryClient()
  const { getTitle } = useSessionTitles(scope)
  const title = getTitle(sessionId)
  const persistedId = draft ? undefined : sessionId
  const { data: session } = useSession(canChat ? persistedId : undefined)
  const hostedHistoryId = provider === 'volcengine' ? persistedId : undefined
  const hostedHistory = useSessionMessages(hostedHistoryId)
  useDefaultConversationTitles(
    scope,
    canChat && persistedId ? [persistedId] : [],
  )

  const request = useCallback<typeof fetch>(
    async (input, init) => {
      const { pathname } = new URL(String(input), window.location.origin)
      const creating = init?.method === 'POST' && pathname === OV_PATHS.sessions
      // VikingBot's sidebar identifies Web conversations by their session ID.
      let response = await requestStudioModule(
        input,
        creating
          ? { ...init, body: JSON.stringify({ session_id: sessionId }) }
          : init,
      )
      // Recover if creation succeeded server-side but its response was lost.
      if (creating && response.status === 409) {
        response = await requestStudioModule(
          OV_PATH_BUILDERS.session(sessionId),
          { signal: init.signal },
        )
      }
      if (response.ok && init?.method === 'POST') {
        if (creating) {
          void queryClient.invalidateQueries({ queryKey: ['sessions'] })
        } else if (pathname === OV_PATH_BUILDERS.sessionMessages(sessionId)) {
          void queryClient.invalidateQueries({
            queryKey: ['conversation-first-message', scope, sessionId],
          })
        }
      }
      return response
    },
    [queryClient, scope, sessionId],
  )

  return (
    <OpenVikingProvider baseURL="" fetch={request}>
      <Console
        className={styles.thread}
        modes={['conversation']}
        scopeKey={scope}
        locale={i18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
        slots={{
          toolbar: (
            <div className="flex items-center justify-between gap-4 text-left">
              <h2 className="truncate text-sm font-medium text-foreground">
                {draft && title === sessionId
                  ? t('threadList.newSession')
                  : title}
              </h2>
              {canChat ? <MemoryImpact session={session} /> : null}
            </div>
          ),
        }}
        conversation={{
          ...(canChat
            ? {
                sessions: {
                  items: [],
                  currentId: persistedId,
                  onSelect: (id) => {
                    if (draft && id === sessionId) onPersisted?.()
                  },
                  onNew: () => {},
                },
              }
            : {
                messages: sharedMessages(hostedHistory.data ?? []),
                composer: false,
                messageList:
                  hostedHistoryId && hostedHistory.isPending ? (
                    <div
                      role="status"
                      className="py-6 text-center text-sm text-muted-foreground"
                    >
                      {t('threadList.loading')}
                    </div>
                  ) : hostedHistory.error ? (
                    <div
                      role="alert"
                      className="py-6 text-center text-sm text-destructive"
                    >
                      {t('chat.historyLoadFailed', {
                        error: hostedHistory.error.message,
                      })}
                    </div>
                  ) : undefined,
              }),
          onCopy: (text) => void copyTextToClipboard(text),
          emptyState: <ThreadEmpty canChat={canChat} />,
          labels: {
            thinking: t('chat.generating'),
            reasoning: t('chat.reasoning'),
            copy: t('chat.copy'),
            placeholder: t('chat.placeholder'),
            send: t('chat.send'),
            stop: t('chat.cancel'),
          },
        }}
      />
    </OpenVikingProvider>
  )
}

function ThreadEmpty({ canChat }: { canChat: boolean }) {
  const { t } = useTranslation('sessions')
  const [showBackground, setShowBackground] = useState(false)

  useEffect(() => {
    if ('requestIdleCallback' in window) {
      const id = window.requestIdleCallback(() => setShowBackground(true))
      return () => window.cancelIdleCallback(id)
    }
    const id = globalThis.setTimeout(() => setShowBackground(true), 200)
    return () => clearTimeout(id)
  }, [])

  return (
    <div className="isolate relative flex grow flex-col items-center justify-center gap-3">
      {showBackground && (
        <div className="pointer-events-none absolute inset-0 -z-10 opacity-40">
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
