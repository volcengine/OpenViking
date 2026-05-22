import { useTranslation } from 'react-i18next'
import { createFileRoute } from '@tanstack/react-router'
import { ExternalLinkIcon } from 'lucide-react'

import { OAuthOtpForm } from '#/components/oauth-setup-dialog'

export const Route = createFileRoute('/oauth/setup')({
  component: OAuthSetupPage,
})

const OAUTH_DOCS_URL =
  'https://github.com/volcengine/OpenViking/blob/main/docs/en/guides/11-oauth.md'

function OAuthSetupPage() {
  const { t } = useTranslation('oauthSetup')
  return (
    <div className="mx-auto w-full max-w-3xl space-y-6 p-4 sm:p-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">
          {t('page.title')}
        </h1>
        <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
          {t('page.intro')}
        </p>
        <a
          href={OAUTH_DOCS_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
        >
          {t('page.docsLink')}
          <ExternalLinkIcon className="size-3.5" />
        </a>
      </header>

      <OAuthOtpForm />
    </div>
  )
}
