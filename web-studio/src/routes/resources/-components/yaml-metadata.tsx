import { ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export function YamlMetadata({
  rawFrontmatter,
  defaultOpen = false,
}: {
  rawFrontmatter: string
  defaultOpen?: boolean
}) {
  const { t } = useTranslation('resources')
  return (
    <details
      open={defaultOpen}
      className="not-prose group rounded-md border bg-background/40"
    >
      <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 text-[11px] font-medium text-muted-foreground transition-colors marker:hidden hover:bg-muted/40 hover:text-foreground">
        <ChevronRight className="size-3.5 shrink-0 transition-transform group-open:rotate-90" />
        {t('filePreview.yamlMetadata.rawYaml')}
      </summary>
      <div className="border-t bg-background/70 p-3">
        <pre className="max-h-[32rem] min-h-32 overflow-auto whitespace-pre rounded-md border bg-muted/20 p-3 font-mono text-xs leading-5 text-foreground">
          <code>{rawFrontmatter}</code>
        </pre>
      </div>
    </details>
  )
}
