import { OpenVikingProvider } from '@openviking/react/client'
import { FileBrowser } from '@openviking/react/modules/file-browser'
import type { FileBrowserProps } from '@openviking/react/modules/file-browser'
import '@openviking/react/modules/file-browser/style.css'
import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { requestWorkbenchModule } from '../lib/module-request'
import styles from '../workbench.module.css'

export function WorkbenchFileBrowser({
  previewActions,
  ...props
}: FileBrowserProps & { previewActions?: ReactNode }) {
  const { t } = useTranslation('filesystem')
  const { connection, serverMode } = useAppConnection()
  const [initialUri] = useState(props.defaultExpandedUri)
  const browserRef = useRef<HTMLDivElement>(null)
  const [toolbar, setToolbar] = useState<HTMLElement | null>(null)

  useEffect(() => {
    const browser = browserRef.current
    if (!browser) return

    // FileBrowser replaces its preview header when the selected resource or
    // tab changes. Scope the host action to this instance's current header.
    const syncToolbar = () => {
      const header = browser.querySelector<HTMLElement>(
        '.ov-file-browser__detail .ov-file-preview__header',
      )
      setToolbar(
        header?.querySelector<HTMLElement>('.ov-file-preview__toolbar') ??
          header ??
          null,
      )
    }
    const observer = new MutationObserver(syncToolbar)
    observer.observe(browser, { childList: true, subtree: true })
    syncToolbar()
    return () => observer.disconnect()
  }, [])

  return (
    <OpenVikingProvider baseURL="" fetch={requestWorkbenchModule}>
      <div ref={browserRef} className="flex min-h-0 min-w-0 flex-1">
        <FileBrowser
          {...props}
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
            entries: {
              connectAgent: t('ide.connectAgent'),
              importResources: t('explorer.addResource'),
            },
          }}
        />
        {toolbar && previewActions
          ? createPortal(previewActions, toolbar)
          : null}
      </div>
    </OpenVikingProvider>
  )
}
