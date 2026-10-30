import { cn } from '#/lib/utils'

import { CopyButton } from './copy-button'

type CodeBlockProps = {
  code: string
  /** Caption above the code, e.g. a filename or "Terminal". */
  label?: string
  /** Wrap long lines (at spaces where possible) instead of scrolling. */
  wrap?: boolean
  className?: string
}

/**
 * Monospace block with a copy button and an optional caption. Without a
 * caption the button sits in its own column, so it never covers the code.
 */
export function CodeBlock({
  code,
  label,
  wrap = false,
  className,
}: CodeBlockProps) {
  return (
    <div
      className={cn(
        'min-w-0 overflow-hidden rounded-lg border bg-muted/30',
        className,
      )}
    >
      {label ? (
        <div className="flex items-center justify-between gap-2 border-b bg-muted/40 py-1 pr-1.5 pl-3">
          <span className="truncate font-mono text-xs text-muted-foreground">
            {label}
          </span>
          <CopyButton value={code} />
        </div>
      ) : null}
      <div className="flex items-start">
        <pre
          className={cn(
            'min-w-0 flex-1 overflow-x-auto p-3 font-mono text-xs leading-5',
            wrap
              ? 'whitespace-pre-wrap [overflow-wrap:anywhere]'
              : 'whitespace-pre',
            !label && 'pr-2',
          )}
        >
          <code>{code}</code>
        </pre>
        {label ? null : (
          <CopyButton value={code} className="mt-2.5 mr-2 shrink-0" />
        )}
      </div>
    </div>
  )
}
