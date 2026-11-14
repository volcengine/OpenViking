import { OpenVikingProvider } from '@openviking/react/client'
import { useTheme } from 'next-themes'
import type { ReactNode } from 'react'
import { useLayoutEffect } from 'react'
import { useTranslation } from 'react-i18next'

export function StudioUiProvider({ children }: { children: ReactNode }) {
  const { resolvedTheme } = useTheme()
  const { i18n } = useTranslation()
  const locale = (i18n.resolvedLanguage ?? i18n.language).startsWith('zh')
    ? 'zh-CN'
    : 'en-US'

  useLayoutEffect(() => {
    if (resolvedTheme !== 'light' && resolvedTheme !== 'dark') return

    // Ark's file viewer and Markdown renderer read the theme from the body.
    const body = document.body
    const previousTheme = body.getAttribute('arco-theme')
    body.setAttribute('arco-theme', resolvedTheme)
    return () => {
      if (previousTheme === null) body.removeAttribute('arco-theme')
      else body.setAttribute('arco-theme', previousTheme)
    }
  }, [resolvedTheme])

  return <OpenVikingProvider locale={locale}>{children}</OpenVikingProvider>
}
