import { useTranslation } from 'react-i18next'

export function TtlExpiry({ expiresAt }: { expiresAt?: string | null }) {
  const { t, i18n } = useTranslation('settings')
  // An older server may omit this field; that does not establish that TTL is off.
  if (expiresAt === undefined) return null
  const date = expiresAt ? new Date(expiresAt) : null
  return (
    <span className="block text-xs text-muted-foreground">
      {date && Number.isFinite(date.getTime()) ? (
        <>
          {t('ttl.expiresAt')}{' '}
          <time dateTime={expiresAt!}>
            {new Intl.DateTimeFormat(i18n.resolvedLanguage, {
              dateStyle: 'medium',
              timeStyle: 'short',
            }).format(date)}
          </time>
        </>
      ) : (
        t('ttl.noExpiry')
      )}
    </span>
  )
}
