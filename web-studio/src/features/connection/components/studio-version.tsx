import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { stripVersionPrefix } from '#/shared/lib/version'
import { useTranslation } from 'react-i18next'

/**
 * The version of the OpenViking server the Studio is connected to.
 *
 * The server reports it on `/health`; the connection layer already keeps it in
 * the shared health state, so the admin console and the workbench can both
 * show the deployed version without an extra request.
 */
export function StudioVersion() {
  const { t } = useTranslation('appShell')
  const { version } = useStudioService()
  if (!version) return null
  return (
    <span
      className="truncate font-mono text-[11px] leading-4 text-muted-foreground/70 group-data-[collapsible=icon]:hidden"
      title={t('footer.versionTitle')}
    >
      {t('footer.version', { version: stripVersionPrefix(version) })}
    </span>
  )
}
