import { OpenVikingProvider } from '@openviking/react/client'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'

export function StudioUiProvider({ children }: { children: ReactNode }) {
  const { i18n } = useTranslation()
  const locale = (i18n.resolvedLanguage ?? i18n.language).startsWith('zh')
    ? 'zh-CN'
    : 'en-US'

  return <OpenVikingProvider locale={locale}>{children}</OpenVikingProvider>
}
