import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { gatewayDocsUrl } from '../-lib/client-guides'
import { requestManagementUnlock } from '../-lib/management-key'
import { useGateway } from '../-lib/use-gateway'
import { CodeBlock } from './code-block'
import { Notice } from './notice'

const GENERATE_KEY_COMMAND = 'openssl rand -base64 32'

export function ManagementUnlockButton() {
  const { t } = useTranslation('gateway')
  return (
    <Button
      type="button"
      size="sm"
      variant="outline"
      onClick={requestManagementUnlock}
    >
      {t('management.title')}
    </Button>
  )
}

export function AccessNotices({ objectsTab }: { objectsTab: boolean }) {
  const { t, i18n } = useTranslation('gateway')
  const { access, connection, readOnly } = useGateway()
  const dismissedKey = `gateway-public-warning:${connection.baseUrl}`
  const [dismissed, setDismissed] = React.useState(() => {
    try {
      return sessionStorage.getItem(dismissedKey) === '1'
    } catch {
      return false
    }
  })
  const dismiss = () => {
    setDismissed(true)
    try {
      sessionStorage.setItem(dismissedKey, '1')
    } catch {
      /* Storage may be unavailable. */
    }
  }
  return (
    <>
      {!dismissed &&
      access?.warnings.includes('public_openviking_without_management_key') ? (
        <Notice
          tone="warning"
          title={t('management.publicTitle')}
          action={
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                nativeButton={false}
                render={
                  <a
                    href={gatewayDocsUrl('operations', i18n.resolvedLanguage)}
                    target="_blank"
                    rel="noreferrer"
                  />
                }
              >
                {t('unavailable.docs')}
              </Button>
              <Button size="sm" variant="ghost" onClick={dismiss}>
                {t('management.dismiss')}
              </Button>
            </div>
          }
        >
          <p>{t('management.publicDescription')}</p>
          <CodeBlock
            code={`OPENVIKING_GATEWAY_MANAGEMENT_KEY=${t('management.keyPlaceholder')}`}
            label={t('unavailable.environment')}
          />
          <p>
            {t('management.generate')} <code>{GENERATE_KEY_COMMAND}</code>
          </p>
        </Notice>
      ) : null}
      {objectsTab && access?.managed ? (
        readOnly ? (
          <Notice tone="info" action={<ManagementUnlockButton />}>
            <p>{t('management.readOnly')}</p>
            {['admin', 'root'].includes(access.role) ? (
              <p>{t('management.adminReadOnly')}</p>
            ) : null}
          </Notice>
        ) : (
          <Notice tone="info">{t('management.shared')}</Notice>
        )
      ) : null}
    </>
  )
}
