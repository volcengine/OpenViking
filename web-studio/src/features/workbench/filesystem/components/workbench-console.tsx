import { OpenVikingProvider, OV_PATHS } from '@openviking/react/client'
import type { VikingBotProps } from '@openviking/react/components/viking-bot'
import { Console } from '@openviking/react/modules/console'
import type { ConsoleMode } from '@openviking/react/modules/console'
import '@openviking/react/modules/console/style.css'
import { useQueryClient } from '@tanstack/react-query'
import { useCallback } from 'react'
import type { ReactNode } from 'react'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useInvalidateVikingFs } from '#/features/workbench/resources/hooks/viking-fm'
import { requestStudioModule } from '#/shared/api/module-request'
import type { ResourceOpenHandler } from '../lib/types'

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
  onFilesystemWrite,
  toolbar,
}: {
  conversation?: Partial<VikingBotProps>
  mode: ConsoleMode
  onModeChange?: (mode: ConsoleMode) => void
  onOpenResource: ResourceOpenHandler
  onFilesystemWrite?: () => void
  toolbar: ReactNode
}) {
  const { identityScopeKey } = useAppConnection()
  const queryClient = useQueryClient()
  const { invalidateAll } = useInvalidateVikingFs()
  const request = useCallback<typeof fetch>(
    async (input, init) => {
      const response = await requestStudioModule(input, init)
      const url = new URL(String(input), window.location.origin)
      if (
        response.ok &&
        init?.method !== 'GET' &&
        filesystemWrites.has(url.pathname)
      ) {
        void invalidateAll()
        onFilesystemWrite?.()
      }
      if (
        response.ok &&
        init?.method === 'POST' &&
        /^\/api\/v1\/sessions(?:\/[^/]+\/messages)?$/.test(url.pathname)
      ) {
        void queryClient.invalidateQueries({ queryKey: ['sessions'] })
        void queryClient.invalidateQueries({
          queryKey: ['conversation-first-message', identityScopeKey],
        })
      }
      return response
    },
    [invalidateAll, onFilesystemWrite, queryClient, identityScopeKey],
  )

  return (
    <OpenVikingProvider baseURL="" fetch={request}>
      <Console
        modes={conversation ? undefined : ['command']}
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
