import { Link } from '@tanstack/react-router'
import { KeyRoundIcon, RefreshCwIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '#/components/ui/button'
import { useAppConnection } from '#/hooks/use-app-connection'
import type { GatewayError } from '../-lib/api'
import { gatewayErrorMessage } from '../-lib/localize'
import { CodeBlock } from './code-block'
import { EnableGatewayCard } from './gateway-enable'
import { SetupCard } from './setup-card'

const CONFIG_FILE = 'ov.conf'

export function GatewayConnect({ invalid = false }: { invalid?: boolean }) {
  const { t } = useTranslation('gateway')
  return (
    <SetupCard
      title={t(invalid ? 'access.invalid' : 'access.title')}
      description={t(
        invalid ? 'access.invalidDescription' : 'access.description',
      )}
      actions={
        <Button nativeButton={false} render={<Link to="/settings" />}>
          <KeyRoundIcon />
          {t('access.action')}
        </Button>
      }
    />
  )
}

export function GatewayUnavailable({
  error,
  onRetry,
  checkAccess,
  retrying = false,
}: {
  error: GatewayError
  onRetry: () => void
  /** Asks for /access again; resolves true when the gateway answers. */
  checkAccess: () => Promise<boolean>
  retrying?: boolean
}) {
  const { t } = useTranslation('gateway')
  const { connectionRole, serverMode, openvikingUrl, isGatewayHost } =
    useAppConnection()
  const isRoot = serverMode === 'dev' || connectionRole === 'root'
  // On an OpenViking server only root can change ov.conf; a standalone
  // gateway host keeps showing the instructions to its admins as well.
  const canConfigure = isRoot || (isGatewayHost && connectionRole === 'admin')
  const retry = (
    <Button variant="outline" size="sm" disabled={retrying} onClick={onRetry}>
      <RefreshCwIcon className={retrying ? 'animate-spin' : undefined} />
      {t('actions.retry')}
    </Button>
  )
  if (error.status === 401 && error.reason !== 'management_key_invalid')
    return <GatewayConnect invalid />
  if (error.reason === 'not_enabled' || error.status === 404) {
    if (!canConfigure)
      return (
        <SetupCard
          title={t('unavailable.notEnabled.title')}
          description={t('unavailable.contact')}
          actions={retry}
        />
      )
    const instructions = (
      <SetupCard
        title={t('unavailable.notEnabled.title')}
        description={t('unavailable.notEnabled.description')}
        actions={retry}
      >
        <CodeBlock
          label={t('unavailable.terminal')}
          code="openviking-server init"
        />
        <CodeBlock label={CONFIG_FILE} code={'{"gateway":{"enabled":true}}'} />
      </SetupCard>
    )
    // Studio edits ov.conf through the admin API, which needs a root key.
    if (isGatewayHost || serverMode !== 'api_key') return instructions
    return (
      <EnableGatewayCard
        retry={retry}
        retrying={retrying}
        onOpenGateway={checkAccess}
        fallback={instructions}
      />
    )
  }
  if (error.reason === 'trusted_mode_unsupported')
    return (
      <SetupCard
        title={t('unavailable.trusted.title')}
        description={t('unavailable.trusted.description')}
        actions={retry}
      >
        <CodeBlock
          label={CONFIG_FILE}
          code={'{"server":{"auth_mode":"api_key"}}'}
        />
      </SetupCard>
    )
  // Only a standalone gateway has an OpenViking URL and gateway.conf to fix.
  if (error.reason === 'openviking_unreachable' && isGatewayHost)
    return (
      <SetupCard
        title={t('unavailable.openvikingUnreachable.title', {
          url: openvikingUrl,
        })}
        description={t('unavailable.openvikingUnreachable.description')}
        actions={retry}
      />
    )
  return (
    <SetupCard
      title={t('unavailable.failed.title')}
      description={gatewayErrorMessage(t, error)}
      actions={retry}
    />
  )
}
