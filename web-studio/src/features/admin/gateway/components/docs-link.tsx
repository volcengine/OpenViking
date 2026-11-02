import { ExternalLinkIcon } from 'lucide-react'
import type * as React from 'react'
import { useTranslation } from 'react-i18next'

import { cn } from '#/shared/lib/utils'
import { Button } from '#/shared/ui/button'

import type { DocsSection } from '#/features/admin/gateway/lib/client-guides'
import { gatewayDocsUrl } from '#/features/admin/gateway/lib/client-guides'

type DocsLinkProps = {
  section: DocsSection
  /** Defaults to "Docs" for a button and "Learn more" for text. */
  children?: React.ReactNode
  /** An outline button for actions, or a link inside running text. */
  variant?: 'button' | 'text'
  className?: string
}

/** Opens a section of the gateway docs, in the UI language, in a new tab. */
export function DocsLink({
  section,
  children,
  variant = 'button',
  className,
}: DocsLinkProps) {
  const { t, i18n } = useTranslation('gateway')
  const href = gatewayDocsUrl(section, i18n.resolvedLanguage)
  if (variant === 'text')
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        className={cn(
          'inline-flex items-center gap-1 font-medium text-foreground underline underline-offset-4',
          className,
        )}
      >
        {children ?? t('docs.learnMore')}
        <ExternalLinkIcon aria-hidden className="size-3" />
      </a>
    )
  return (
    <Button
      size="sm"
      variant="outline"
      className={className}
      nativeButton={false}
      render={<a href={href} target="_blank" rel="noreferrer" />}
    >
      <ExternalLinkIcon />
      {children ?? t('docs.link')}
    </Button>
  )
}
