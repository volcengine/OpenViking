import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { CompileError } from '#/features/workbench/compile/components/shared'
import { fetchCapabilities } from '#/features/workbench/compile/lib/api'
import { Button } from '#/shared/ui/button'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'

export function useCompileCapability() {
  const { identityScopeKey } = useAppConnection()
  return useQuery({
    queryKey: ['compile-capability', identityScopeKey],
    queryFn: ({ signal }) => fetchCapabilities(signal),
    retry: false,
  })
}

export function CapabilityNotice({
  capability,
}: {
  capability: ReturnType<typeof useCompileCapability>
}) {
  const { t, i18n } = useTranslation('compile')
  const { openConnectionSettings } = useAppConnection()
  if (capability.isError)
    return (
      <div className="space-y-2">
        <p className="text-sm">{t('capability.failed')}</p>
        <CompileError
          error={capability.error}
          retry={() => void capability.refetch()}
        />
      </div>
    )
  if (capability.isPending)
    return (
      <p role="status" className="text-sm text-muted-foreground">
        {t('capability.checking')}
      </p>
    )
  if (capability.data.can_create) return null
  const reason = capability.data.reason_code
  const missingKey = reason === 'API_KEY_REQUIRED'
  const unconfigured = reason === 'NOT_CONFIGURED'
  return (
    <div
      role="status"
      className="space-y-3 rounded-lg border bg-muted/30 p-4 text-sm"
    >
      <p>
        {t(
          missingKey
            ? 'capability.keyRequired'
            : unconfigured
              ? 'capability.notConfigured'
              : 'unconfigured',
        )}
      </p>
      <div className="flex flex-wrap gap-3">
        {missingKey && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={openConnectionSettings}
          >
            {t('capability.connection')}
          </Button>
        )}
        {unconfigured && (
          <a
            className="underline underline-offset-4"
            href={`https://docs.openviking.ai/${i18n.language.startsWith('zh') ? 'zh' : 'en'}/api/23-agent-runtime`}
            target="_blank"
            rel="noreferrer"
          >
            {t('capability.adminGuide')}
          </a>
        )}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={capability.isFetching}
          onClick={() => void capability.refetch()}
        >
          {t('retry')}
        </Button>
      </div>
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">
          {t('errors.details')}
        </summary>
        <p className="mt-2 text-xs">{t('capability.technical')}</p>
        {reason && <code className="text-xs">{reason}</code>}
      </details>
    </div>
  )
}
