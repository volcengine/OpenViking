import { Link } from '@tanstack/react-router'
import { ArrowLeftIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { StudioEntry } from '#/platform/entry/paths'
import styles from './auth-layout.module.css'

export function AuthBackLink() {
  const { t } = useTranslation('onboarding')
  return (
    <Link to={StudioEntry.Login} className={`${styles.link} ${styles.back}`}>
      <ArrowLeftIcon size={16} />
      {t('accountAuth.back')}
    </Link>
  )
}
