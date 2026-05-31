import { useMemo } from 'react'
import { ArrowLeft, Folder, Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { cn } from '#/lib/utils'

import { normalizeDirUri, fileNameFromUri } from '../-lib/normalize'
import { useVikingFsList, useDebouncedValue } from '../-hooks/viking-fm'
import type { VikingFsEntry } from '../-types/viking-fm'
import { FilePreview } from './file-preview'
import { ItemColumn } from './item-column'

const VIKING_ROOT_URI = 'viking://'

// Pure controlled view. The browsed directory (currentUri), the item list and
// the cursor (activeIndex) are all owned by FindPalette; this component only
// renders and reports intent via callbacks — no internal focus state, no
// document keyboard listener, no two-way sync ref.
interface DirBrowserProps {
  currentUri: string
  items: VikingFsEntry[]
  activeIndex: number
  loading?: boolean
  errored?: boolean
  onCursorChange: (index: number) => void
  onEnterDir: (uri: string) => void
  onGoBack: () => void
}

export function DirBrowser({
  currentUri,
  items,
  activeIndex,
  loading = false,
  errored = false,
  onCursorChange,
  onEnterDir,
  onGoBack,
}: DirBrowserProps) {
  const { t } = useTranslation('resources')

  // activeIndex can transiently fall out of range while the list reshapes, so
  // the cursor really is nullable (bounds check keeps the union honest).
  const cursorItem =
    activeIndex >= 0 && activeIndex < items.length ? items[activeIndex] : null

  // Peek (right pane) is a pure derivation of the cursor item. Debounced so
  // arrow-scanning a long directory doesn't fire a request per row.
  const subdirUri = cursorItem?.isDir ? normalizeDirUri(cursorItem.uri) : null
  const debouncedSubdirUri = useDebouncedValue(subdirUri, 150)
  const subdirQuery = useVikingFsList(
    debouncedSubdirUri || VIKING_ROOT_URI,
    { output: 'agent', showAllHidden: true, nodeLimit: 200 },
    Boolean(debouncedSubdirUri),
  )
  const subdirItems = useMemo(() => {
    if (!debouncedSubdirUri || !subdirQuery.data?.entries) return []
    const entries = subdirQuery.data.entries
    const dirs = entries.filter((e) => e.isDir)
    const files = entries.filter((e) => !e.isDir)
    return [...dirs, ...files]
  }, [debouncedSubdirUri, subdirQuery.data])
  const peekPending = Boolean(subdirUri) && subdirUri !== debouncedSubdirUri

  const canGoBack = currentUri !== VIKING_ROOT_URI

  return (
    <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b bg-background/85 px-3 py-2 backdrop-blur-sm">
        <button
          type="button"
          onClick={() => {
            if (canGoBack) onGoBack()
          }}
          disabled={!canGoBack}
          className={cn(
            'inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors',
            canGoBack
              ? 'text-foreground/75 hover:bg-muted hover:text-foreground'
              : 'cursor-not-allowed text-muted-foreground/45',
          )}
        >
          <ArrowLeft className="size-3.5" />
          <span>{t('dirBrowser.back')}</span>
        </button>
        <div className="max-w-[55%] truncate rounded-md bg-blue-500/10 px-2.5 py-1 text-sm font-semibold text-blue-700 dark:text-blue-300">
          {currentUri}
        </div>
      </div>
      <div className="flex min-h-0 flex-1 min-w-0 justify-start overflow-hidden bg-[linear-gradient(180deg,color-mix(in_oklch,var(--muted)_35%,transparent),transparent_18%)]">
        {loading ? (
          <div className="flex flex-1 items-center justify-center">
            <Loader2 className="size-4 animate-spin text-muted-foreground" />
          </div>
        ) : errored ? (
          <div
            role="alert"
            className="flex flex-1 items-center justify-center px-6 text-sm text-destructive"
          >
            {t('dirBrowser.error')}
          </div>
        ) : (
          <>
            <ItemColumn
              className="w-[clamp(15rem,35vw,22rem)] shrink-0"
              label={fileNameFromUri(currentUri.replace(/\/$/, '')) || '/'}
              items={items}
              activeIndex={activeIndex}
              t={t}
              onSelect={(entry) => onEnterDir(entry.uri)}
              onSelectFile={(_, i) => onCursorChange(i)}
            />
            <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden border-l">
              {cursorItem && !cursorItem.isDir ? (
                <FilePreview
                  file={cursorItem}
                  onClose={() => {}}
                  showCloseButton={false}
                />
              ) : peekPending || subdirQuery.isLoading ? (
                <div className="flex flex-1 items-center justify-center">
                  <Loader2 className="size-4 animate-spin text-muted-foreground" />
                </div>
              ) : cursorItem?.isDir && subdirQuery.isError ? (
                <div
                  role="alert"
                  className="flex flex-1 items-center justify-center px-6 text-sm text-destructive"
                >
                  {t('dirBrowser.error')}
                </div>
              ) : subdirItems.length > 0 ? (
                <ItemColumn
                  className="min-w-0 flex-1"
                  label={cursorItem ? fileNameFromUri(cursorItem.uri) : ''}
                  items={subdirItems}
                  activeIndex={-1}
                  t={t}
                  onSelect={() => {
                    if (cursorItem?.isDir) onEnterDir(cursorItem.uri)
                  }}
                  onSelectFile={() => {
                    if (cursorItem?.isDir) onEnterDir(cursorItem.uri)
                  }}
                />
              ) : cursorItem?.isDir ? (
                <div className="flex flex-1 items-center justify-center px-6">
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
                <div className="flex flex-1 items-center justify-center px-6 text-sm text-muted-foreground/60">
                  {t('dirBrowser.empty.title')}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
