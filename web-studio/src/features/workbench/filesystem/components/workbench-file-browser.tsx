import { OpenVikingProvider } from '@openviking/react/client'
import { FileBrowser } from '@openviking/react/modules/file-browser'
import type {
  FileBrowserHandle,
  FileBrowserProps,
} from '@openviking/react/modules/file-browser'
import '@openviking/react/modules/file-browser/style.css'
import { useLayoutEffect, useRef, useState } from 'react'
import type { Ref } from 'react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { requestStudioModule } from '#/shared/api/module-request'
import styles from '../workbench.module.css'

export interface DirectoryLevelRequest {
  uri: string
  level: 'l0' | 'l1'
}

export function WorkbenchFileBrowser({
  ref,
  directoryLevel,
  ...props
}: FileBrowserProps & {
  ref?: Ref<FileBrowserHandle>
  directoryLevel?: DirectoryLevelRequest
}) {
  const { t } = useTranslation('filesystem')
  const { connection, serverMode } = useAppConnection()
  const [initialUri] = useState(props.defaultExpandedUri)
  const containerRef = useRef<HTMLDivElement>(null)
  const appliedLevel = useRef<DirectoryLevelRequest>(undefined)

  useLayoutEffect(() => {
    const container = containerRef.current
    if (
      !container ||
      !directoryLevel ||
      props.uri !== directoryLevel.uri ||
      directoryLevel === appliedLevel.current
    )
      return

    // The shared browser has no controlled summary-level prop. Apply the link's
    // level once the matching directory tabs render, then allow manual switching.
    const selectLevel = () => {
      const panel = container.querySelector(
        `[role="tabpanel"][id$="-panel-${CSS.escape(directoryLevel.uri)}"]`,
      )
      const levels = panel?.querySelectorAll<HTMLButtonElement>(
        '.ov-file-preview__levels [role="tab"]',
      )
      const tab = levels?.[directoryLevel.level === 'l0' ? 0 : 1]
      if (!tab) return false
      appliedLevel.current = directoryLevel
      if (tab.getAttribute('aria-selected') !== 'true') tab.click()
      return true
    }
    if (selectLevel()) return

    const observer = new MutationObserver(() => {
      if (selectLevel()) observer.disconnect()
    })
    observer.observe(container, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['id'],
    })
    return () => observer.disconnect()
  }, [directoryLevel, props.uri])

  return (
    <OpenVikingProvider baseURL="" fetch={requestStudioModule}>
      <div ref={containerRef} className="flex min-h-0 min-w-0 flex-1">
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
              importResources: t('explorer.addResource'),
            },
          }}
        />
      </div>
    </OpenVikingProvider>
  )
}
