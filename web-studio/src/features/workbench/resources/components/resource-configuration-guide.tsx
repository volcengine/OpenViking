import { ChevronRight, ExternalLink } from 'lucide-react'
import type { ReactNode } from 'react'

type ResourceConfigurationGuideProps = {
  children: ReactNode
  documentationLabel?: string
  documentationUrl?: string
  title: string
}

export function ResourceConfigurationGuide({
  children,
  documentationLabel,
  documentationUrl,
  title,
}: ResourceConfigurationGuideProps) {
  return (
    <details className="group rounded-md border border-border/50 bg-background/30 px-3 py-2">
      <summary className="flex cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-foreground marker:hidden">
        <ChevronRight className="size-3.5 transition-transform group-open:rotate-90" />
        {title}
      </summary>
      <div className="mt-2 space-y-2 border-t border-border/40 pt-2 text-xs text-muted-foreground">
        {children}
        {documentationUrl && documentationLabel ? (
          <a
            href={documentationUrl}
            target="_blank"
            rel="noreferrer"
            className="inline-flex w-fit items-center gap-1 font-medium text-foreground underline underline-offset-2"
          >
            {documentationLabel}
            <ExternalLink className="size-3" />
          </a>
        ) : null}
      </div>
    </details>
  )
}
