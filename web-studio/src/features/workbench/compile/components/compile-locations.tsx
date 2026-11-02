import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { CompileError } from '#/features/workbench/compile/components/shared'
import {
  sourceUris,
  targetError,
} from '#/features/workbench/compile/lib/locations'
import { DirectoryPickerDialog } from '#/features/workbench/resources/components/directory-picker-dialog'
import { fetchFsStat } from '#/features/workbench/resources/lib/api'
import { copyTextToClipboard } from '#/shared/lib/clipboard'
import { Button } from '#/shared/ui/button'
import { Input } from '#/shared/ui/input'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Copy, FolderOpen, Plus, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

function CopyPathButton({ value }: { value: string }) {
  const { t } = useTranslation('compile')
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      className="absolute right-1 top-1/2 -translate-y-1/2 text-muted-foreground"
      title={t('copyPath')}
      aria-label={`${t('copyPath')} ${value.trim()}`.trim()}
      disabled={!value.trim()}
      onClick={() => {
        void copyTextToClipboard(value.trim())
          .then(() => toast.success(t('copied')))
          .catch(() => toast.error(t('copyFailed')))
      }}
    >
      <Copy className="size-4" />
    </Button>
  )
}

export function CompileLocations({
  sources,
  to,
  onSources,
  onTarget,
  attempted,
  sourceError,
  destinationError,
}: {
  sources: string
  to: string
  onSources: (value: string) => void
  onTarget: (value: string) => void
  attempted: boolean
  sourceError: string
  destinationError: string
}) {
  const { t } = useTranslation('compile')
  const { identityScopeKey, connection } = useAppConnection()
  const userRoot = `viking://user/${connection.userId || 'default'}/resources/`
  const [picker, setPicker] = useState<'source' | 'target' | null>(null)
  const rows = sources ? sources.split('\n') : ['']
  const [checkedTarget, setCheckedTarget] = useState(to)
  useEffect(() => {
    const timer = setTimeout(() => setCheckedTarget(to), 350)
    return () => clearTimeout(timer)
  }, [to])
  const target = useQuery({
    queryKey: ['compile-target-stat', identityScopeKey, checkedTarget],
    queryFn: () => fetchFsStat(checkedTarget, { throwOnError: true }),
    enabled: !targetError(checkedTarget),
    retry: false,
  })
  const notFound =
    target.isError && (target.error as { code?: string }).code === 'NOT_FOUND'
  return (
    <>
      <section
        className="space-y-2 border-t pt-5"
        aria-labelledby="compile-sources-heading"
      >
        <label
          id="compile-sources-heading"
          htmlFor="compile-sources"
          className="block text-sm font-medium"
        >
          {t('sources')} <span className="text-destructive">*</span>
        </label>
        <p id="source-hint" className="text-xs text-muted-foreground">
          {t('sourceHint')}
        </p>
        <div className="space-y-2">
          {rows.map((uri, index) => (
            <div key={index} className="flex items-center gap-2">
              <div className="relative min-w-0 flex-1">
                <Input
                  id={
                    index === 0 ? 'compile-sources' : `compile-source-${index}`
                  }
                  aria-label={t('sourceNumber', { number: index + 1 })}
                  aria-invalid={attempted && !!sourceError}
                  aria-describedby="source-hint error-sources"
                  placeholder={t('sourcePlaceholder')}
                  value={uri}
                  className="pr-10 font-mono text-xs"
                  onChange={(event) =>
                    onSources(
                      rows
                        .map((value, i) =>
                          i === index ? event.target.value : value,
                        )
                        .join('\n'),
                    )
                  }
                />
                <CopyPathButton value={uri} />
              </div>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label={t('removeSource', { uri: uri || index + 1 })}
                onClick={() =>
                  onSources(rows.filter((_, i) => i !== index).join('\n'))
                }
              >
                <X className="size-4" />
              </Button>
            </div>
          ))}
        </div>
        {attempted && sourceError && (
          <p
            id="error-sources"
            role="alert"
            className="text-xs text-destructive"
          >
            {sourceError}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setPicker('source')}
          >
            <FolderOpen className="size-4" />
            {t('browse')}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onSources([...rows, ''].join('\n'))}
          >
            <Plus className="size-4" />
            {t('addSource')}
          </Button>
        </div>
      </section>
      <section className="space-y-2" aria-labelledby="compile-target-heading">
        <label
          id="compile-target-heading"
          htmlFor="compile-target"
          className="block text-sm font-medium"
        >
          {t('target')} <span className="text-destructive">*</span>
        </label>
        <div className="flex gap-2">
          <div className="relative min-w-0 flex-1">
            <Input
              id="compile-target"
              aria-invalid={attempted && !!destinationError}
              aria-describedby="target-hint error-to"
              className="pr-10 font-mono text-xs"
              required
              value={to}
              onChange={(event) => onTarget(event.target.value)}
              placeholder={t('targetPlaceholder')}
            />
            <CopyPathButton value={to} />
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={() => setPicker('target')}
          >
            {t('browse')}
          </Button>
        </div>
        {attempted && destinationError && (
          <p id="error-to" role="alert" className="text-xs text-destructive">
            {destinationError}
          </p>
        )}
        <div
          id="target-hint"
          className="text-xs text-muted-foreground"
          role="status"
        >
          {targetError(to) ? (
            t('targetHint')
          ) : checkedTarget !== to || target.isPending ? (
            t('checkingTarget')
          ) : notFound ? (
            t('targetNew')
          ) : target.isError ? (
            <CompileError
              error={target.error}
              retry={() => void target.refetch()}
            />
          ) : target.data.isDir ? (
            t('targetExists')
          ) : (
            t('targetNotDirectory')
          )}
        </div>
      </section>
      <DirectoryPickerDialog
        identityScopeKey={identityScopeKey}
        userResourceRoot={userRoot}
        open={!!picker}
        onOpenChange={(open) => {
          if (!open) setPicker(null)
        }}
        value={picker === 'target' && to ? to : userRoot}
        emptyAction={
          picker === 'source' ? (
            <Link
              to="/filesystem"
              search={{ uri: userRoot }}
              className="text-sm text-primary underline underline-offset-4"
            >
              {t('openFilesystem')}
            </Link>
          ) : undefined
        }
        onSelect={(uri) => {
          if (picker === 'target') onTarget(uri.replace(/\/$/, ''))
          else onSources(sourceUris([...rows, uri].join('\n')).join('\n'))
          setPicker(null)
        }}
      />
    </>
  )
}
