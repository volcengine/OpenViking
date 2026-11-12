import { OpenVikingProvider, OV_PATHS } from '@openviking/react/client'
import type { VikingBotProps } from '@openviking/react/components/viking-bot'
import { Console } from '@openviking/react/modules/console'
import type { ConsoleMode } from '@openviking/react/modules/console'
import '@openviking/react/modules/console/style.css'
import { useCallback } from 'react'
import type { ReactNode } from 'react'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useInvalidateVikingFs } from '#/features/workbench/resources/hooks/viking-fm'
import { getOvClient, isOvClientError } from '#/shared/api/ov-client/index'
import type { ResourceOpenHandler } from '../lib/types'
import styles from '../workbench.module.css'

const filesystemWrites = new Set<string>([
  OV_PATHS.fsCp,
  OV_PATHS.fsMkdir,
  OV_PATHS.fsMv,
  OV_PATHS.fsRm,
  OV_PATHS.contentWrite,
  OV_PATHS.resources,
  OV_PATHS.skills,
  OV_PATHS.compile,
])

export function WorkbenchConsole({
  conversation,
  mode,
  onModeChange,
  onOpenResource,
  toolbar,
}: {
  conversation?: Partial<VikingBotProps>
  mode: ConsoleMode
  onModeChange?: (mode: ConsoleMode) => void
  onOpenResource: ResourceOpenHandler
  toolbar: ReactNode
}) {
  const { identityScopeKey } = useAppConnection()
  const { invalidateAll } = useInvalidateVikingFs()
  const request = useCallback<typeof fetch>(
    async (input, init) => {
      // Console builds the API request. Studio applies the active data-plane
      // connection, authentication, and gateway routing to that request.
      const url = new URL(String(input), window.location.origin)
      const method = init?.method ?? 'GET'
      try {
        const { status, data } = await getOvClient().instance.request({
          url: url.pathname + url.search,
          method,
          data: init?.body,
          headers: Object.fromEntries(new Headers(init?.headers)),
          signal: init?.signal ?? undefined,
        })
        if (method !== 'GET' && filesystemWrites.has(url.pathname)) {
          void invalidateAll()
        }
        return new Response(JSON.stringify(data), { status })
      } catch (error) {
        if (!isOvClientError(error) || !error.statusCode) throw error
        // Keep Studio's error envelope available to the shared Console client.
        const body = {
          status: 'error',
          error: {
            code: error.code,
            message: error.message,
            details: error.details,
          },
        }
        return new Response(JSON.stringify(body), {
          status: error.statusCode >= 400 ? error.statusCode : 400,
        })
      }
    },
    [invalidateAll],
  )

  return (
    <OpenVikingProvider baseURL="" fetch={request}>
      <Console
        className={conversation ? undefined : styles.commandOnlyConsole}
        mode={mode}
        onModeChange={onModeChange}
        conversation={conversation}
        scopeKey={identityScopeKey}
        slots={{ toolbar }}
        onOpenUri={(uri) => {
          if (/^https?:\/\//i.test(uri)) {
            window.open(uri, '_blank', 'noopener,noreferrer')
          } else if (/^viking:\/\//i.test(uri)) {
            void onOpenResource(uri)
          }
        }}
      />
    </OpenVikingProvider>
  )
}
