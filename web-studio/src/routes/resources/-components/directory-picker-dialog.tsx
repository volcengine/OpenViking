import { useQuery } from '@tanstack/react-query'
import { ChevronRight, FolderIcon, FolderOpen } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { ScrollArea } from '#/components/ui/scroll-area'
import { Spinner } from '#/components/ui/spinner'
import type { VikingFsEntry } from '../-types/viking-fm'
import { normalizeDirUri, normalizeFsEntries } from '../-lib/normalize'
import { getFsLs, getOvResult, isOvClientError } from '#/lib/ov-client'

function getErrorMessage(error: unknown): string {
  if (isOvClientError(error)) {
    return `${error.code}: ${error.message}`
  }
  if (error instanceof Error) {
    return error.message
  }
  return String(error)
}

interface DirectoryPickerDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  value: string
  onSelect: (uri: string) => void
}

function parseBreadcrumbs(uri: string): Array<{ label: string; uri: string }> {
  const crumbs: Array<{ label: string; uri: string }> = [
    { label: 'viking://', uri: 'viking://' },
  ]
  const body = uri.slice('viking://'.length).replace(/\/$/, '')
  if (!body) return crumbs

  const segments = body.split('/')
  for (let i = 0; i < segments.length; i++) {
    crumbs.push({
      label: segments[i],
      uri: `viking://${segments.slice(0, i + 1).join('/')}/`,
    })
  }
  return crumbs
}

export function DirectoryPickerDialog({
  open,
  onOpenChange,
  value,
  onSelect,
}: DirectoryPickerDialogProps) {
  const { t } = useTranslation('addResource')
  const [browseUri, setBrowseUri] = useState(value)

  useEffect(() => {
    if (open) {
      setBrowseUri(value)
    }
  }, [open, value])

  const normalizedUri = normalizeDirUri(browseUri)

  const dirQuery = useQuery({
    queryKey: ['dir-picker', normalizedUri],
    queryFn: async () => {
      const result = await getOvResult(
        getFsLs({
          query: {
            uri: normalizedUri,
            show_all_hidden: false,
          },
        }),
      )
      const entries = normalizeFsEntries(result, normalizedUri)
      return entries.filter((e: VikingFsEntry) => e.isDir)
    },
    enabled: open,
  })

  const breadcrumbs = parseBreadcrumbs(normalizedUri)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('dirPicker.title')}</DialogTitle>
        </DialogHeader>

        {/* Breadcrumb */}
        <div className="flex flex-wrap items-center gap-1 text-sm">
          {breadcrumbs.map((crumb, i) => (
            <span key={crumb.uri} className="flex items-center gap-1">
              {i > 0 && <ChevronRight className="size-3 text-muted-foreground" />}
              <button
                type="button"
                className={`rounded px-1 py-0.5 hover:bg-muted ${
                  i === breadcrumbs.length - 1 ? 'font-medium text-foreground' : 'text-muted-foreground'
                }`}
                onClick={() => setBrowseUri(crumb.uri)}
              >
                {crumb.label}
              </button>
            </span>
          ))}
        </div>

        {/* Directory list */}
        <ScrollArea className="h-[300px] rounded-md border">
          {dirQuery.isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Spinner className="size-5" />
            </div>
          ) : dirQuery.isError ? (
            <div className="px-4 py-8 text-center text-sm text-destructive">
              {t('dirPicker.error')}: {getErrorMessage(dirQuery.error)}
            </div>
          ) : dirQuery.data && dirQuery.data.length > 0 ? (
            <div className="p-1">
              {dirQuery.data.map((entry: VikingFsEntry) => {
                const name = entry.uri.replace(/\/$/, '').split('/').pop() || entry.uri
                return (
                  <button
                    key={entry.uri}
                    type="button"
                    className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-left text-sm transition-colors hover:bg-muted"
                    onClick={() => setBrowseUri(normalizeDirUri(entry.uri))}
                  >
                    <FolderIcon className="size-4 shrink-0 text-muted-foreground" />
                    <span className="truncate">{name}</span>
                  </button>
                )
              })}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center gap-2 py-12 text-muted-foreground">
              <FolderOpen className="size-8" />
              <p className="text-sm">{t('dirPicker.empty')}</p>
            </div>
          )}
        </ScrollArea>

        {/* Selected path */}
        <p className="truncate text-xs text-muted-foreground">
          {t('dirPicker.selected')} <span className="font-mono">{normalizedUri}</span>
        </p>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t('dirPicker.cancel')}
          </Button>
          <Button
            onClick={() => {
              onSelect(normalizedUri)
              onOpenChange(false)
            }}
          >
            {t('dirPicker.select')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
