import { OpenVikingProvider } from '@openviking/react/client'
import { FileBrowser } from '@openviking/react/modules/file-browser'
import type { FileBrowserProps } from '@openviking/react/modules/file-browser'
import '@openviking/react/modules/file-browser/style.css'
import { useState } from 'react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { requestWorkbenchModule } from '../lib/module-request'
import styles from '../workbench.module.css'

export function WorkbenchFileBrowser(props: FileBrowserProps) {
  const { t } = useTranslation('filesystem')
  const { connection, serverMode } = useAppConnection()
  const [initialUri] = useState(props.defaultExpandedUri)
  return (
    <OpenVikingProvider baseURL="" fetch={requestWorkbenchModule}>
      <FileBrowser
        {...props}
        defaultExpandedUri={initialUri}
        className={styles.fileBrowser}
        agentConnect={
          serverMode === 'trusted'
            ? undefined
            : { apiKey: connection.apiKey, baseUrl: connection.baseUrl }
        }
        labels={{
          entries: {
            connectAgent: t('ide.connectAgent'),
            importResources: t('explorer.addResource'),
          },
        }}
      />
    </OpenVikingProvider>
  )
}
