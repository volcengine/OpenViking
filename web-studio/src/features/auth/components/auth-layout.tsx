import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { getRouterBasePath } from '#/shared/lib/public-path'
import { OpenVikingMark } from '#/shared/components/openviking-mark'
import { Button } from '#/shared/ui/button'
import teamIllustration from '#/assets/workbench/team.png'
import styles from './auth-layout.module.css'

export function AuthLayout({ children }: { children: ReactNode }) {
  const { t, i18n } = useTranslation('onboarding')
  return (
    <div className={styles.layout}>
      <header className={styles.header}>
        <a href={getRouterBasePath()} className={styles.brand}>
          <OpenVikingMark className="size-8" />
          <span>{t('appShell:studioSettings.brand')}</span>
        </a>
        <Button
          variant="ghost"
          className="h-11 min-w-11 px-3"
          onClick={() =>
            void i18n.changeLanguage(
              i18n.resolvedLanguage?.startsWith('zh') ? 'en' : 'zh-CN',
            )
          }
        >
          {t('apiLogin.language')}
        </Button>
      </header>
      <main className={styles.body}>
        <section className={styles.content}>
          <div className={styles.form}>{children}</div>
        </section>
        <aside className={styles.visual} aria-hidden="true">
          <img
            src={teamIllustration}
            alt=""
            width={1536}
            height={1024}
            className={styles.illustration}
          />
          <p>{t('apiLogin.tagline')}</p>
        </aside>
      </main>
    </div>
  )
}
