import { useTranslation } from 'react-i18next'
import { Checkbox } from '#/shared/ui/checkbox'
import styles from './auth-layout.module.css'

export function AuthConsent({
  termsUrl,
  checked,
  onChange,
}: {
  termsUrl: string
  checked: boolean
  onChange: (checked: boolean) => void
}) {
  const { t } = useTranslation('onboarding')
  return (
    <div className={styles.consent}>
      <div className={styles.consentRow}>
        <label className={styles.consentLabel}>
          <Checkbox checked={checked} onCheckedChange={onChange} />
          <span>{t('consent.agree')}</span>
        </label>
        <a
          href={termsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className={styles.link}
        >
          {t('consent.terms')}
        </a>
        <span className={styles.pendingPolicy}>
          {t('consent.privacyPending')}
        </span>
      </div>
    </div>
  )
}
