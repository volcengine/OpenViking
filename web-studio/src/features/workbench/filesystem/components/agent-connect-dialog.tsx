import { AgentConnect } from '@openviking/react/components/agent-connect'
import '@openviking/react/components/agent-connect/style.css'
import { useTranslation } from 'react-i18next'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { Dialog, DialogContent, DialogTitle } from '#/shared/ui/dialog'

export function AgentConnectDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { connection, identityScopeKey } = useAppConnection()
  const { t, i18n } = useTranslation('filesystem')
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="gap-0 p-0 sm:max-w-5xl"
        aria-describedby={undefined}
      >
        <DialogTitle className="sr-only">{t('ide.connectAgent')}</DialogTitle>
        <AgentConnect
          key={identityScopeKey}
          apiKey={connection.apiKey}
          baseUrl={connection.baseUrl}
          locale={i18n.language.startsWith('zh') ? 'zh-CN' : 'en-US'}
        />
      </DialogContent>
    </Dialog>
  )
}
