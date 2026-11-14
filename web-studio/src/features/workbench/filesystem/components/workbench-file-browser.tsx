import { OpenVikingProvider } from '@openviking/react/client'
import { FileBrowser } from '@openviking/react/modules/file-browser'
import type {
  FileBrowserHandle,
  FileBrowserProps,
} from '@openviking/react/modules/file-browser'
import '@openviking/react/modules/file-browser/style.css'
import { useState } from 'react'
import type { RefObject } from 'react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { requestStudioModule } from '#/shared/api/module-request'
import styles from '../workbench.module.css'

export function WorkbenchFileBrowser({
  ref,
  ...props
}: FileBrowserProps & {
  ref?: RefObject<FileBrowserHandle | null>
}) {
  const { t } = useTranslation('filesystem')
  const { connection, serverMode } = useAppConnection()
  const [initialUri] = useState(props.defaultExpandedUri)

  return (
    <OpenVikingProvider baseURL="" fetch={requestStudioModule}>
      <div className="flex min-h-0 min-w-0 flex-1">
        <FileBrowser
          {...props}
          ref={ref}
          defaultExpandedUri={initialUri}
          className={styles.fileBrowser}
          agentConnect={
            serverMode === 'trusted'
              ? undefined
              : {
                  apiKey: connection.apiKey,
                  baseUrl: connection.baseUrl,
                  showOverseasTag: false,
                }
          }
          labels={{
            actionBar: {
              connectAgent: t('ide.connectAgent'),
              importResources: t('importMenu.trigger'),
            },
          }}
        />
      </div>
    </OpenVikingProvider>
  )
}
