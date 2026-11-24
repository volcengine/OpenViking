import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { createConnectionRoleProbeKey } from '#/features/connection/lib/connection-runtime'
import { copyTextToClipboard } from '#/shared/lib/clipboard'
import { Button } from '#/shared/ui/button'
import {
  Popover,
  PopoverContent,
  PopoverTitle,
  PopoverTrigger,
} from '#/shared/ui/popover'
import {
  CheckIcon,
  CopyIcon,
  EyeIcon,
  EyeOffIcon,
  KeyRoundIcon,
} from 'lucide-react'
import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

function maskKey(key: string) {
  return key.length > 12 ? `${key.slice(0, 4)}…${key.slice(-4)}` : '••••••••'
}

export function StudioApiKeyMenu({
  managementMode,
  isGatewayHost = false,
}: {
  managementMode: boolean
  isGatewayHost?: boolean
}) {
  const { t } = useTranslation('appShell')
  const { connection, connectionRole, serverMode } = useAppConnection()
  const apiKey =
    managementMode || serverMode === 'trusted'
      ? connection.adminApiKey || (isGatewayHost ? connection.apiKey : '')
      : connection.apiKey
  const identityLabel = managementMode
    ? connectionRole === 'admin'
      ? t('apiKey.account')
      : t('apiKey.credentialType')
    : t('apiKey.userId')
  const identityValue = managementMode
    ? connectionRole === 'admin'
      ? connection.accountId
      : t(connectionRole === 'root' ? 'apiKey.root' : 'apiKey.management')
    : connection.userId
  return (
    <CredentialPopover
      key={`${managementMode}:${createConnectionRoleProbeKey(connection, serverMode)}`}
      apiKey={apiKey}
      identityLabel={identityLabel}
      identityValue={identityValue}
      credentialLabel={
        serverMode === 'trusted' ? t('apiKey.trusted') : undefined
      }
    />
  )
}

function CredentialPopover({
  apiKey,
  identityLabel,
  identityValue,
  credentialLabel,
}: {
  apiKey: string
  identityLabel: string
  identityValue: string
  credentialLabel?: string
}) {
  const { t } = useTranslation('appShell')
  const [open, setOpen] = React.useState(false)
  const [visible, setVisible] = React.useState(false)
  const [copied, setCopied] = React.useState(false)
  function changeOpen(next: boolean) {
    setOpen(next)
    setVisible(false)
    setCopied(false)
  }
  async function copy() {
    try {
      await copyTextToClipboard(apiKey)
      setCopied(true)
      toast.success(t('settings:toast.copied'))
    } catch {
      toast.error(t('settings:toast.copyFailed'))
    }
  }
  return (
    <Popover open={open} onOpenChange={changeOpen}>
      <PopoverTrigger
        aria-label={t('apiKey.title')}
        className="inline-flex h-9 items-center justify-center gap-2 rounded-md px-2 text-sm font-medium outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring data-popup-open:bg-muted sm:px-2.5"
      >
        <KeyRoundIcon className="size-4" />
        <span className="hidden sm:inline">{t('apiKey.title')}</span>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        sideOffset={10}
        className="w-[min(25rem,calc(100vw-1.5rem))] gap-5 rounded-xl p-5 shadow-lg"
      >
        <PopoverTitle className="text-lg font-semibold">
          {t('apiKey.title')}
        </PopoverTitle>
        <dl className="flex min-w-0 flex-col gap-5">
          <div className="space-y-1.5">
            <dt className="text-xs text-muted-foreground">{identityLabel}</dt>
            <dd className="break-all text-sm font-medium">
              {identityValue || t('apiKey.unset')}
            </dd>
          </div>
          <div className="space-y-1.5">
            <dt className="text-xs text-muted-foreground">
              {credentialLabel || t('apiKey.title')}
            </dt>
            <dd className="flex min-w-0 items-end justify-between gap-3">
              {apiKey ? (
                <>
                  <code className="min-w-0 flex-1 break-all font-mono text-sm leading-8">
                    {visible ? apiKey : maskKey(apiKey)}
                  </code>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={t(visible ? 'apiKey.hide' : 'apiKey.show')}
                      title={t(visible ? 'apiKey.hide' : 'apiKey.show')}
                      aria-pressed={visible}
                      onClick={() => setVisible((value) => !value)}
                    >
                      {visible ? (
                        <EyeOffIcon className="size-4" />
                      ) : (
                        <EyeIcon className="size-4" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={t('apiKey.copy')}
                      title={t(copied ? 'apiKey.copied' : 'apiKey.copy')}
                      onClick={() => void copy()}
                    >
                      {copied ? (
                        <CheckIcon className="size-4 text-emerald-600 dark:text-emerald-400" />
                      ) : (
                        <CopyIcon className="size-4" />
                      )}
                    </Button>
                  </div>
                </>
              ) : (
                <span className="text-sm text-muted-foreground">
                  {t('apiKey.empty')}
                </span>
              )}
            </dd>
          </div>
        </dl>
      </PopoverContent>
    </Popover>
  )
}
