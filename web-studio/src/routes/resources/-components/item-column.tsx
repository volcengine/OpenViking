import { ChevronRight, FileText, Folder } from 'lucide-react'
import type { TFunction } from 'i18next'

import { cn } from '#/lib/utils'
import { useTransientScrollbar } from '#/hooks/use-transient-scrollbar'
import type { VikingFsEntry } from '../-types/viking-fm'

export function ItemColumn({
  className,
  label,
  items,
  activeIndex,
  t,
  onSelect,
}: {
  className?: string
  label: string
  items: VikingFsEntry[]
  activeIndex: number
  t: TFunction<'resources'>
  onSelect: (entry: VikingFsEntry, index: number) => void
}) {
  const { isScrolling, onScroll } = useTransientScrollbar()

  return (
    <div className={cn('flex min-w-0 flex-col overflow-hidden', className)}>
      <div className="flex min-h-11 items-center gap-1.5 border-b bg-blue-500/8 px-3 py-2 text-xs font-semibold tracking-[0.08em] text-blue-700/80 uppercase backdrop-blur-sm dark:text-blue-300/85">
        <Folder className="size-3.5 text-blue-600/75 dark:text-blue-300/75" />
        <span className="truncate normal-case tracking-normal text-sm font-semibold text-blue-700/90 dark:text-blue-200">
          {label}
        </span>
      </div>
      <div
        className="scrollbar-fade min-h-0 flex-1 overflow-y-auto overscroll-contain"
        data-scrolling={isScrolling || undefined}
        onScroll={onScroll}
      >
        {items.length === 0 ? (
          <div className="flex h-full items-center justify-center px-6 py-10">
            <div className="max-w-[13rem] text-center">
              <div className="mx-auto mb-3 flex size-10 items-center justify-center rounded-2xl bg-muted/60 text-muted-foreground/70 shadow-inner">
                <Folder className="size-4" />
              </div>
              <p className="text-sm font-medium text-foreground/70">
                {t('dirBrowser.empty.title')}
              </p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground/75">
                {t('dirBrowser.empty.subtitle')}
              </p>
            </div>
          </div>
        ) : (
          items.map((entry, i) => {
            const isActive = i === activeIndex
            const isDir = entry.isDir

            return (
              <button
                key={entry.uri}
                type="button"
                data-active={isActive}
                className={cn(
                  'group relative flex w-full items-center gap-2.5 border-b border-border/40 px-3 py-2 text-left text-sm transition-colors',
                  isActive
                    ? 'bg-primary/8 text-foreground'
                    : 'text-foreground/80 hover:bg-muted/35',
                )}
                onClick={() => onSelect(entry, i)}
              >
                {isActive && (
                  <span className="absolute inset-y-1 left-0 w-0.5 rounded-r bg-primary" />
                )}
                {isDir ? (
                  <Folder
                    className={cn(
                      'size-3.5 shrink-0 transition-colors',
                      isActive ? 'text-primary/80' : 'text-muted-foreground',
                    )}
                  />
                ) : (
                  <FileText
                    className={cn(
                      'size-3.5 shrink-0',
                      isActive
                        ? 'text-blue-600/75 dark:text-blue-300/75'
                        : 'text-muted-foreground/65',
                    )}
                  />
                )}
                <span className="truncate font-medium">{entry.name}</span>
                {isDir && (
                  <ChevronRight className="ml-auto size-3 shrink-0 text-muted-foreground/45 transition-transform group-hover:translate-x-0.5" />
                )}
              </button>
            )
          })
        )}
      </div>
    </div>
  )
}
