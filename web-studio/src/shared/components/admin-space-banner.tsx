import { BoxesIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export function AdminSpaceBanner({
  accountId,
  showScope = false,
}: {
  accountId: string
  showScope?: boolean
}) {
  const { t } = useTranslation('appShell')
  if (!accountId) return null

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-[var(--studio-accent-tint)] px-3 py-2 text-sm text-[var(--studio-accent-text)]">
      <BoxesIcon className="size-4 shrink-0" />
      <span>{t('adminNavigation.currentSpace', { space: accountId })}</span>
      {showScope ? (
        <span className="ml-1">{t('adminNavigation.spaceScope')}</span>
      ) : null}
    </div>
  )
}
