import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import {
  createFileRoute,
  useBlocker,
  useNavigate,
} from '@tanstack/react-router'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTheme } from 'next-themes'
import { useTranslation } from 'react-i18next'
import type { RJSFValidationError } from '@rjsf/utils'
import {
  CableIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  FilePlus2Icon,
  FileTextIcon,
  FolderIcon,
  FolderPlusIcon,
  GitBranchIcon,
  ListIcon,
  Loader2Icon,
  Maximize2Icon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  SaveIcon,
  SearchIcon,
  Trash2Icon,
} from 'lucide-react'
import { toast } from 'sonner'

import { CodeEditor } from '#/components/code-editor'
import { Alert, AlertDescription, AlertTitle } from '#/components/ui/alert'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '#/components/ui/alert-dialog'
import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { Input } from '#/components/ui/input'
import { Label } from '#/components/ui/label'
import { ScrollArea } from '#/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '#/components/ui/select'
import { Textarea } from '#/components/ui/textarea'
import { useAppConnection } from '#/hooks/use-app-connection'
import { cn } from '#/lib/utils'
import { isOvClientError } from '#/lib/ov-client'
import { AddResourceForm } from '#/routes/resources/-components/add-resource-page'
import { ResourceUploadProvider } from '#/routes/resources/-hooks/use-resource-upload'
import type { VikingFsEntry } from '#/routes/resources/-types/viking-fm'

import { LiveContextInspector } from './-components/live-context'
import { SchemaArgumentsForm } from './-components/schema-arguments-form'
import {
  createDirectory,
  createText,
  hashText,
  isLiveContextDocument,
  listDirectory,
  loadMcpCatalog,
  loadLiveMounts,
  mountLiveContext,
  moveLiveContext,
  mutateOrdinaryResource,
  parseLiveContextMetadata,
  readTextDocument,
  saveText,
  statResource,
  unmountLiveContext,
} from './-lib/api'
import type {
  ArrangerView,
  LiveContextMount,
  LiveMountIndex,
  McpCatalog,
  MountInput,
  MutationKind,
} from './-lib/model'
import { initialSchemaArguments, validateSchemaArguments } from './-lib/schema'
import type { SchemaFormObject } from './-lib/schema'
import {
  joinVikingUri,
  nameFromVikingUri,
  normalizeVikingUri,
  parentVikingUri,
  resolveMountNodeUri,
  toggleExpandedUri,
} from './-lib/uri'

const ContextGraph = lazy(() =>
  import('./-components/context-graph').then((module) => ({
    default: module.ContextGraph,
  })),
)

type ArrangerSearch = {
  selected?: string
  uri?: string
  view?: ArrangerView
}

type ActionKind = 'copy' | 'file' | 'mkdir' | 'mount' | 'move'

type ActionState = {
  kind: ActionKind
  parentUri?: string
  source?: VikingFsEntry
} | null

export const Route = createFileRoute('/arranger')({
  validateSearch: (search: Record<string, unknown>): ArrangerSearch => ({
    selected: typeof search.selected === 'string' ? search.selected : undefined,
    uri: typeof search.uri === 'string' ? search.uri : undefined,
    view:
      search.view === 'graph' || search.view === 'list'
        ? search.view
        : undefined,
  }),
  component: ArrangerRoute,
})

function ArrangerRoute() {
  return (
    <ResourceUploadProvider>
      <ArrangerWorkbench />
    </ResourceUploadProvider>
  )
}

function ArrangerWorkbench() {
  const { t } = useTranslation('arranger')
  const { identityScopeKey, openConnectionSettings } = useAppConnection()
  const navigate = useNavigate({ from: Route.fullPath })
  const search = Route.useSearch()
  const queryClient = useQueryClient()
  const [pathDraft, setPathDraft] = useState(
    search.uri || 'viking://resources/arranger',
  )
  const rootUri = useMemo(() => {
    try {
      return search.uri ? normalizeVikingUri(search.uri) : ''
    } catch {
      return ''
    }
  }, [search.uri])
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(rootUri ? [rootUri] : []),
  )
  const [directoryLimits, setDirectoryLimits] = useState<
    Record<string, number>
  >({})
  const [filter, setFilter] = useState('')
  const [action, setAction] = useState<ActionState>(null)
  const [addResourceOpen, setAddResourceOpen] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const view = search.view || 'graph'

  const rootQuery = useQuery({
    enabled: Boolean(rootUri),
    queryKey: ['arranger', identityScopeKey, rootUri, 'stat'],
    queryFn: () => statResource(rootUri),
    retry: false,
  })
  const mountsQuery = useQuery({
    enabled: Boolean(rootUri && rootQuery.data?.isDir),
    queryKey: ['arranger', identityScopeKey, rootUri, 'mounts'],
    queryFn: () => loadLiveMounts(rootUri),
    placeholderData: (previous) => previous,
    refetchInterval: 15_000,
  })
  const catalogQuery = useQuery({
    enabled: Boolean(rootUri && rootQuery.data?.isDir),
    queryKey: ['arranger', identityScopeKey, 'mcp-catalog'],
    queryFn: loadMcpCatalog,
    staleTime: 30_000,
  })
  const expandedUris = useMemo(() => [...expanded].sort(), [expanded])
  const directoryQueries = useQueries({
    queries: expandedUris.map((uri) => ({
      enabled: Boolean(rootQuery.data?.isDir),
      queryKey: [
        'arranger',
        identityScopeKey,
        rootUri,
        'directory',
        uri,
        directoryLimits[uri] || 200,
      ],
      queryFn: () => listDirectory(uri, 0, directoryLimits[uri] || 200),
      refetchInterval: 15_000,
    })),
  })
  const entries = useMemo(() => {
    const values = new Map<string, VikingFsEntry>()
    for (const query of directoryQueries) {
      for (const entry of query.data?.entries || []) {
        values.set(entry.uri, entry)
      }
    }
    return [...values.values()].sort((left, right) =>
      left.uri.localeCompare(right.uri),
    )
  }, [directoryQueries])
  const directoriesWithMore = useMemo(
    () =>
      new Set(
        expandedUris.filter(
          (_, index) => directoryQueries[index]?.data?.hasMore,
        ),
      ),
    [directoryQueries, expandedUris],
  )
  const liveIndex: LiveMountIndex = mountsQuery.data || {
    complete: false,
    mounts: new Map(),
  }
  const rootEntry = rootQuery.data || null
  const selected = useMemo(() => {
    if (!search.selected) return null
    return (
      entries.find((entry) => entry.uri === search.selected) ||
      (rootEntry?.uri === search.selected ? rootEntry : null)
    )
  }, [entries, rootEntry, search.selected])

  useEffect(() => {
    setExpanded(new Set(rootUri ? [rootUri] : []))
    setDirectoryLimits({})
  }, [rootUri, identityScopeKey])

  const refresh = useCallback(async () => {
    await queryClient.invalidateQueries({
      queryKey: ['arranger', identityScopeKey, rootUri],
    })
  }, [identityScopeKey, queryClient, rootUri])

  const choose = useCallback(
    (entry: VikingFsEntry) => {
      void navigate({
        search: (previous) => ({ ...previous, selected: entry.uri }),
        replace: true,
      })
    },
    [navigate],
  )

  const toggleExpanded = useCallback((uri: string) => {
    setExpanded((current) => toggleExpandedUri(current, uri))
  }, [])

  const submitPath = () => {
    try {
      const uri = normalizeVikingUri(pathDraft)
      void navigate({
        search: (previous) => ({
          ...previous,
          selected: undefined,
          uri,
        }),
      })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }

  const setView = (next: ArrangerView) => {
    void navigate({
      search: (previous) => ({ ...previous, view: next }),
      replace: true,
    })
  }

  const filteredEntries = useMemo(() => {
    const needle = filter.trim().toLowerCase()
    if (!needle) return entries
    return entries.filter(
      (entry) =>
        entry.name.toLowerCase().includes(needle) ||
        entry.uri.toLowerCase().includes(needle),
    )
  }, [entries, filter])

  const selectedDirectory = selected
    ? selected.isDir
      ? selected
      : entries.find(
          (entry) =>
            normalizeVikingUri(entry.uri) === parentVikingUri(selected.uri),
        )
    : rootEntry
  const createParent = selectedDirectory?.uri || rootUri

  if (!search.uri) {
    return (
      <div className="flex min-h-[calc(100svh-6rem)] items-start justify-center px-6 py-12">
        <div className="w-full max-w-3xl space-y-8">
          <div className="space-y-2">
            <h1 className="text-2xl font-semibold">{t('title')}</h1>
            <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
              {t('description')}
            </p>
          </div>
          <div className="space-y-2">
            <Label>{t('start.pathTitle')}</Label>
            <PathInput
              value={pathDraft}
              onChange={setPathDraft}
              onSubmit={submitPath}
            />
            <p className="text-xs text-muted-foreground">
              {t('start.pathHint')}
            </p>
          </div>
          <div className="grid gap-6 border-y py-5 sm:grid-cols-2">
            <div className="flex gap-3">
              <FileTextIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
              <div>
                <h2 className="text-sm font-medium">
                  {t('start.staticTitle')}
                </h2>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {t('start.staticDescription')}
                </p>
              </div>
            </div>
            <div className="flex gap-3">
              <CableIcon className="mt-0.5 size-4 shrink-0 text-emerald-600" />
              <div>
                <h2 className="text-sm font-medium">{t('start.liveTitle')}</h2>
                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  {t('start.liveDescription')}
                </p>
              </div>
            </div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div
      className={cn(
        'flex h-[calc(100svh-6rem)] min-h-[560px] flex-col overflow-hidden border bg-background',
        isFullscreen && 'fixed inset-0 z-50 h-svh min-h-0 border-0',
      )}
    >
      <header className="flex shrink-0 flex-wrap items-center gap-2 border-b px-3 py-2">
        <div className="hidden w-64 shrink-0 xl:block">
          <h1 className="text-sm font-semibold">{t('title')}</h1>
          <p className="truncate text-[11px] text-muted-foreground">
            {t('shortDescription')}
          </p>
        </div>
        <PathInput
          value={pathDraft}
          onChange={setPathDraft}
          onSubmit={submitPath}
        />
        <div className="ml-auto flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon-sm"
            title={t('actions.refresh')}
            onClick={() => void refresh()}
          >
            <RefreshCwIcon />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            title={t('actions.fullscreen')}
            onClick={() => setIsFullscreen((value) => !value)}
          >
            <Maximize2Icon />
          </Button>
        </div>
      </header>

      {rootQuery.isLoading ? (
        <CenteredStatus icon={Loader2Icon} label={t('loading')} spin />
      ) : rootQuery.error ? (
        <div className="p-6">
          <Alert variant="destructive">
            <AlertTitle>{t('errors.loadRoot')}</AlertTitle>
            <AlertDescription>
              {rootQuery.error instanceof Error
                ? rootQuery.error.message
                : String(rootQuery.error)}
            </AlertDescription>
            {isOvClientError(rootQuery.error) &&
              rootQuery.error.statusCode === 404 && (
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-3"
                  onClick={() => {
                    void createDirectory(rootUri)
                      .then(refresh)
                      .catch((error) =>
                        toast.error(
                          error instanceof Error
                            ? error.message
                            : String(error),
                        ),
                      )
                  }}
                >
                  <FolderPlusIcon />
                  {t('actions.createRoot')}
                </Button>
              )}
            {(!isOvClientError(rootQuery.error) ||
              rootQuery.error.statusCode !== 404) && (
              <Button
                variant="outline"
                size="sm"
                className="mt-3"
                onClick={openConnectionSettings}
              >
                {t('actions.connectionSettings')}
              </Button>
            )}
          </Alert>
        </div>
      ) : !rootEntry?.isDir ? (
        <CenteredStatus
          icon={FileTextIcon}
          label={t('errors.rootNotDirectory')}
        />
      ) : (
        <>
          <div className="flex shrink-0 flex-wrap items-center gap-1 border-b px-3 py-2">
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                setAction({ kind: 'mkdir', parentUri: createParent })
              }
            >
              <FolderPlusIcon />
              {t('actions.newDirectory')}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                setAction({ kind: 'file', parentUri: createParent })
              }
            >
              <FilePlus2Icon />
              {t('actions.newFile')}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() =>
                setAction({ kind: 'mount', parentUri: createParent })
              }
            >
              <CableIcon />
              {t('actions.mount')}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setAddResourceOpen(true)}
            >
              <PlusIcon />
              {t('actions.addResource')}
            </Button>
            <div className="relative ml-auto min-w-44 flex-1 sm:max-w-64">
              <SearchIcon className="absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
              <Input
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                placeholder={t('searchPlaceholder')}
                className="pl-8"
              />
            </div>
            <div className="flex rounded-md border p-0.5">
              <Button
                variant={view === 'graph' ? 'secondary' : 'ghost'}
                size="icon-sm"
                title={t('views.graph')}
                onClick={() => setView('graph')}
              >
                <GitBranchIcon />
              </Button>
              <Button
                variant={view === 'list' ? 'secondary' : 'ghost'}
                size="icon-sm"
                title={t('views.list')}
                onClick={() => setView('list')}
              >
                <ListIcon />
              </Button>
            </div>
          </div>
          {!liveIndex.complete && (
            <Alert className="m-2 shrink-0 rounded-md">
              <AlertTitle>{t('live.incomplete')}</AlertTitle>
              <AlertDescription>
                {liveIndex.error || t('live.mutationsBlocked')}
              </AlertDescription>
              <Button
                variant="outline"
                size="sm"
                className="mt-3"
                onClick={openConnectionSettings}
              >
                {t('actions.connectionSettings')}
              </Button>
            </Alert>
          )}
          <main className="grid min-h-0 flex-1 grid-cols-1 grid-rows-[minmax(280px,1fr)_minmax(240px,0.8fr)] lg:grid-cols-[260px_minmax(320px,1fr)_360px] lg:grid-rows-1">
            <ScrollArea className="hidden border-r lg:block">
              <ContextTree
                root={rootEntry}
                entries={entries}
                expanded={expanded}
                liveIndex={liveIndex}
                directoriesWithMore={directoriesWithMore}
                selectedUri={selected?.uri}
                onLoadMore={(uri) =>
                  setDirectoryLimits((current) => ({
                    ...current,
                    [uri]: (current[uri] || 200) + 200,
                  }))
                }
                onSelect={choose}
                onToggle={toggleExpanded}
              />
            </ScrollArea>
            <section className="min-h-0 overflow-hidden border-r">
              {view === 'graph' ? (
                <Suspense
                  fallback={
                    <CenteredStatus
                      icon={Loader2Icon}
                      label={t('loading')}
                      spin
                    />
                  }
                >
                  <ContextGraph
                    root={rootEntry}
                    entries={filteredEntries}
                    expanded={expanded}
                    selectedUri={selected?.uri}
                    isLive={(uri) => liveIndex.mounts.has(uri)}
                    onSelect={choose}
                    onToggle={toggleExpanded}
                  />
                </Suspense>
              ) : (
                <ContextList
                  entries={filteredEntries}
                  liveIndex={liveIndex}
                  selectedUri={selected?.uri}
                  onSelect={choose}
                />
              )}
            </section>
            <ResourceInspector
              catalog={catalogQuery.data}
              entry={selected || rootEntry}
              isRoot={(selected || rootEntry).uri === rootUri}
              liveMount={liveIndex.mounts.get((selected || rootEntry).uri)}
              mountsComplete={liveIndex.complete}
              onAction={(kind) =>
                setAction({
                  kind,
                  parentUri: createParent,
                  source: selected || rootEntry,
                })
              }
              onDelete={async () => {
                const entry = selected || rootEntry
                try {
                  const mount = liveIndex.mounts.get(entry.uri)
                  if (mount) {
                    await unmountLiveContext(entry.uri)
                  } else {
                    await mutateOrdinaryResource({
                      kind: 'delete',
                      liveIndex,
                      rootUri,
                      source: entry,
                    })
                  }
                  void navigate({
                    search: (previous) => ({
                      ...previous,
                      selected: rootUri,
                    }),
                    replace: true,
                  })
                  await refresh()
                  toast.success(t('messages.deleted'))
                } catch (error) {
                  toast.error(
                    error instanceof Error ? error.message : String(error),
                  )
                  await refresh()
                  throw error
                }
              }}
              onSaved={refresh}
            />
          </main>
        </>
      )}
      {action && rootEntry && (
        <ActionDialog
          action={action}
          catalog={catalogQuery.data}
          catalogLoading={catalogQuery.isLoading}
          defaultParent={action.parentUri || createParent}
          liveIndex={liveIndex}
          rootUri={rootUri}
          onClose={() => setAction(null)}
          onComplete={async (entryUri) => {
            setAction(null)
            await refresh()
            if (entryUri) {
              try {
                choose(await statResource(entryUri))
              } catch {
                // The refreshed tree remains authoritative.
              }
            }
          }}
        />
      )}
      <Dialog open={addResourceOpen} onOpenChange={setAddResourceOpen}>
        <DialogContent className="max-h-[min(86vh,760px)] gap-0 overflow-hidden p-0 sm:max-w-4xl">
          <DialogHeader className="border-b px-6 py-5">
            <DialogTitle>{t('actions.addResource')}</DialogTitle>
            <DialogDescription>
              {t('dialogs.addResource.description')}
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[calc(min(86vh,760px)-6rem)] overflow-y-auto px-6 py-5">
            <AddResourceForm
              key={createParent}
              initialTargetUri={createParent}
              onSubmitted={() => {
                setAddResourceOpen(false)
                void refresh()
              }}
            />
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function PathInput({
  onChange,
  onSubmit,
  value,
}: {
  onChange: (value: string) => void
  onSubmit: () => void
  value: string
}) {
  const { t } = useTranslation('arranger')
  return (
    <form
      className="flex min-w-0 flex-1 gap-2"
      onSubmit={(event) => {
        event.preventDefault()
        onSubmit()
      }}
    >
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-label={t('pathLabel')}
        className="min-w-0 font-mono text-xs"
      />
      <Button type="submit" size="sm">
        {t('actions.load')}
      </Button>
    </form>
  )
}

function CenteredStatus({
  icon: Icon,
  label,
  spin,
}: {
  icon: typeof FileTextIcon
  label: string
  spin?: boolean
}) {
  return (
    <div className="flex h-full min-h-48 flex-col items-center justify-center gap-3 text-muted-foreground">
      <Icon className={cn('size-6', spin && 'animate-spin')} />
      <span className="text-sm">{label}</span>
    </div>
  )
}

function ContextTree({
  entries,
  directoriesWithMore,
  expanded,
  liveIndex,
  onLoadMore,
  onSelect,
  onToggle,
  root,
  selectedUri,
}: {
  entries: VikingFsEntry[]
  expanded: Set<string>
  liveIndex: LiveMountIndex
  directoriesWithMore: Set<string>
  onLoadMore: (uri: string) => void
  onSelect: (entry: VikingFsEntry) => void
  onToggle: (uri: string) => void
  root: VikingFsEntry
  selectedUri?: string
}) {
  const { t } = useTranslation('arranger')
  const children = useMemo(() => {
    const grouped = new Map<string, VikingFsEntry[]>()
    for (const entry of entries) {
      const parent = parentVikingUri(entry.uri)
      grouped.set(parent, [...(grouped.get(parent) || []), entry])
    }
    return grouped
  }, [entries])

  const renderEntry = (entry: VikingFsEntry, depth: number) => (
    <div key={entry.uri}>
      <button
        type="button"
        className={cn(
          'flex h-8 w-full items-center gap-1.5 pr-2 text-left text-xs hover:bg-muted',
          selectedUri === entry.uri && 'bg-muted font-medium',
        )}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        onClick={() => {
          onSelect(entry)
          if (entry.isDir) onToggle(entry.uri)
        }}
      >
        {entry.isDir ? (
          <span aria-hidden="true">
            {expanded.has(entry.uri) ? (
              <ChevronDownIcon className="size-3.5" />
            ) : (
              <ChevronRightIcon className="size-3.5" />
            )}
          </span>
        ) : (
          <span className="w-3.5" />
        )}
        {liveIndex.mounts.has(entry.uri) ? (
          <CableIcon className="size-4 text-emerald-600" />
        ) : entry.isDir ? (
          <FolderIcon className="size-4 text-amber-600" />
        ) : (
          <FileTextIcon className="size-4 text-muted-foreground" />
        )}
        <span className="truncate">{entry.name}</span>
      </button>
      {entry.isDir && expanded.has(entry.uri) && (
        <>
          {(children.get(normalizeVikingUri(entry.uri)) || []).map((child) =>
            renderEntry(child, depth + 1),
          )}
          {directoriesWithMore.has(entry.uri) && (
            <Button
              variant="ghost"
              size="sm"
              className="ml-7 h-7 text-xs"
              onClick={() => onLoadMore(entry.uri)}
            >
              {t('actions.loadMore')}
            </Button>
          )}
        </>
      )}
    </div>
  )

  return <div className="py-2">{renderEntry(root, 0)}</div>
}

function ContextList({
  entries,
  liveIndex,
  onSelect,
  selectedUri,
}: {
  entries: VikingFsEntry[]
  liveIndex: LiveMountIndex
  onSelect: (entry: VikingFsEntry) => void
  selectedUri?: string
}) {
  return (
    <ScrollArea className="h-full">
      <div className="divide-y">
        {entries.map((entry) => {
          const Icon = liveIndex.mounts.has(entry.uri)
            ? CableIcon
            : entry.isDir
              ? FolderIcon
              : FileTextIcon
          return (
            <button
              key={entry.uri}
              type="button"
              className={cn(
                'flex h-11 w-full items-center gap-3 px-4 text-left hover:bg-muted/60',
                selectedUri === entry.uri && 'bg-muted',
              )}
              onClick={() => onSelect(entry)}
            >
              <Icon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate text-sm">
                {entry.name}
              </span>
              <span className="hidden max-w-1/2 truncate font-mono text-[11px] text-muted-foreground sm:block">
                {entry.uri}
              </span>
            </button>
          )
        })}
      </div>
    </ScrollArea>
  )
}

function ResourceInspector({
  catalog,
  entry,
  isRoot,
  liveMount,
  mountsComplete,
  onAction,
  onDelete,
  onSaved,
}: {
  catalog?: McpCatalog
  entry: VikingFsEntry
  isRoot: boolean
  liveMount?: LiveContextMount
  mountsComplete: boolean
  onAction: (kind: 'copy' | 'mount' | 'move') => void
  onDelete: () => Promise<void>
  onSaved: () => Promise<void>
}) {
  const { t } = useTranslation('arranger')
  const [deleting, setDeleting] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  return (
    <>
      <aside className="flex min-h-0 flex-col bg-muted/15">
        <div className="space-y-2 border-b p-4">
          <div className="flex items-center gap-2">
            <h2 className="min-w-0 flex-1 truncate text-sm font-semibold">
              {entry.name || entry.uri}
            </h2>
            {liveMount && <Badge variant="secondary">{t('live.badge')}</Badge>}
          </div>
          <p className="break-all font-mono text-[11px] text-muted-foreground">
            {entry.uri}
          </p>
          {!isRoot && (
            <div className="flex flex-wrap gap-1 pt-1">
              {liveMount && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onAction('mount')}
                >
                  <PencilIcon />
                  {t('actions.editMount')}
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={!mountsComplete}
                onClick={() => onAction('move')}
              >
                <PencilIcon />
                {t('actions.move')}
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!mountsComplete}
                onClick={() => onAction('copy')}
              >
                <CopyIcon />
                {t('actions.copy')}
              </Button>
              <Button
                variant="destructive"
                size="sm"
                disabled={deleting || !mountsComplete}
                onClick={() => setDeleteOpen(true)}
              >
                <Trash2Icon />
                {t('actions.delete')}
              </Button>
            </div>
          )}
        </div>
        <div className="min-h-0 flex-1">
          {entry.isDir ? (
            <div className="space-y-3 p-4 text-sm">
              <Label>{t('fields.summary')}</Label>
              <p className="whitespace-pre-wrap text-muted-foreground">
                {entry.overview || entry.abstract || t('emptySummary')}
              </p>
            </div>
          ) : (
            <TextInspector
              key={entry.uri}
              catalog={catalog}
              entry={entry}
              liveMount={liveMount}
              onSaved={onSaved}
            />
          )}
        </div>
      </aside>
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('dialogs.delete.title', { name: entry.name || entry.uri })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t(
                liveMount
                  ? 'dialogs.delete.liveDescription'
                  : entry.isDir
                    ? 'dialogs.delete.directoryDescription'
                    : 'dialogs.delete.fileDescription',
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="rounded-md border bg-muted/50 px-3 py-2 font-mono text-xs break-all text-muted-foreground">
            {entry.uri}
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>
              {t('actions.cancel')}
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleting}
              onClick={async (event) => {
                event.preventDefault()
                setDeleting(true)
                try {
                  await onDelete()
                  setDeleteOpen(false)
                } catch {
                  // The parent reports the error and refreshes authoritative data.
                } finally {
                  setDeleting(false)
                }
              }}
            >
              {deleting ? (
                <Loader2Icon className="animate-spin" />
              ) : (
                <Trash2Icon />
              )}
              {deleting
                ? t('dialogs.delete.deleting')
                : t('dialogs.delete.confirm')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function TextInspector({
  catalog,
  entry,
  liveMount,
  onSaved,
}: {
  catalog?: McpCatalog
  entry: VikingFsEntry
  liveMount?: LiveContextMount
  onSaved: () => Promise<void>
}) {
  const { t } = useTranslation('arranger')
  const { resolvedTheme } = useTheme()
  const documentQuery = useQuery({
    queryKey: ['arranger-document', entry.uri, entry.modTime],
    queryFn: () => readTextDocument(entry.uri),
    retry: false,
  })
  const [draft, setDraft] = useState('')
  const [status, setStatus] = useState<
    'conflict' | 'dirty' | 'error' | 'saved' | 'saving'
  >('saved')
  const [savingBeforeLeave, setSavingBeforeLeave] = useState(false)
  const latestRef = useRef('')
  const committedRef = useRef('')
  const hashRef = useRef('')
  const runningRef = useRef(false)
  const mountedRef = useRef(true)

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (!documentQuery.data) return
    if (latestRef.current !== committedRef.current) return
    setDraft(documentQuery.data.content)
    latestRef.current = documentQuery.data.content
    committedRef.current = documentQuery.data.content
    hashRef.current = documentQuery.data.sha256
    setStatus('saved')
  }, [documentQuery.data])

  const flush = useCallback(async () => {
    if (liveMount) return true
    if (runningRef.current) return false
    runningRef.current = true
    try {
      while (latestRef.current !== committedRef.current) {
        const snapshot = latestRef.current
        if (mountedRef.current) setStatus('saving')
        try {
          await saveText(entry.uri, snapshot, hashRef.current)
        } catch (error) {
          if (mountedRef.current) {
            setStatus(
              isOvClientError(error) &&
                error.statusCode === 409 &&
                !(
                  error.details &&
                  typeof error.details === 'object' &&
                  'retryable' in error.details &&
                  error.details.retryable === true
                )
                ? 'conflict'
                : 'error',
            )
          }
          return false
        }
        committedRef.current = snapshot
        hashRef.current = hashText(snapshot)
        if (mountedRef.current && latestRef.current === snapshot) {
          setStatus('saved')
          await onSaved()
        }
      }
      return true
    } finally {
      runningRef.current = false
    }
  }, [entry.uri, liveMount, onSaved])

  const blocker = useBlocker({
    shouldBlockFn: () => latestRef.current !== committedRef.current,
    enableBeforeUnload: () => latestRef.current !== committedRef.current,
    withResolver: true,
  })

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 's') {
        event.preventDefault()
        void flush()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [flush])

  if (documentQuery.isLoading) {
    return <CenteredStatus icon={Loader2Icon} label={t('loading')} spin />
  }
  if (documentQuery.error || !documentQuery.data) {
    return (
      <div className="p-4 text-sm text-destructive">
        {documentQuery.error instanceof Error
          ? documentQuery.error.message
          : t('errors.read')}
      </div>
    )
  }

  const detectedLive =
    Boolean(liveMount) || isLiveContextDocument(documentQuery.data.content)
  const editable =
    !detectedLive &&
    (entry.sizeBytes === null || entry.sizeBytes <= 2 * 1024 * 1024)

  if (detectedLive && liveMount) {
    return (
      <LiveContextInspector
        document={documentQuery.data.content}
        mount={liveMount}
        tool={catalog?.tools.find((item) => item.uri === liveMount.tool_uri)}
      />
    )
  }

  return (
    <>
      <div className="flex h-full min-h-0 flex-col">
        <div className="flex h-9 shrink-0 items-center border-b px-3 text-xs text-muted-foreground">
          {detectedLive ? t('live.readOnly') : t(`save.${status}`)}
          {!detectedLive && (
            <Button
              variant="ghost"
              size="icon-sm"
              className="ml-auto"
              title={t('actions.save')}
              disabled={status === 'saving'}
              onClick={() => void flush()}
            >
              <SaveIcon />
            </Button>
          )}
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <CodeEditor
            initialContent={draft}
            filename={entry.name}
            isDark={resolvedTheme === 'dark'}
            readOnly={!editable}
            lineWrapping
            ariaLabel={entry.name}
            onChange={
              editable
                ? (content) => {
                    setDraft(content)
                    latestRef.current = content
                    if (status !== 'conflict') setStatus('dirty')
                  }
                : undefined
            }
          />
        </div>
      </div>
      <AlertDialog
        open={blocker.status === 'blocked'}
        onOpenChange={(open) => {
          if (!open && !savingBeforeLeave && blocker.status === 'blocked') {
            blocker.reset()
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {t('save.leaveTitle', { name: entry.name })}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {t('save.leaveDescription')}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {(status === 'error' || status === 'conflict') && (
            <p role="alert" className="text-sm text-destructive">
              {t(`save.${status}`)}
            </p>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel
              disabled={savingBeforeLeave}
              onClick={() => {
                if (blocker.status === 'blocked') blocker.reset()
              }}
            >
              {t('save.continueEditing')}
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={savingBeforeLeave}
              onClick={() => {
                if (blocker.status === 'blocked') blocker.proceed()
              }}
            >
              {t('save.discard')}
            </AlertDialogAction>
            <AlertDialogAction
              disabled={savingBeforeLeave}
              onClick={async (event) => {
                event.preventDefault()
                setSavingBeforeLeave(true)
                const saved = await flush()
                setSavingBeforeLeave(false)
                if (saved && blocker.status === 'blocked') blocker.proceed()
              }}
            >
              {savingBeforeLeave ? (
                <Loader2Icon className="animate-spin" />
              ) : (
                <SaveIcon />
              )}
              {t('save.saveAndLeave')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function ActionDialog({
  action,
  catalog,
  catalogLoading,
  defaultParent,
  liveIndex,
  onClose,
  onComplete,
  rootUri,
}: {
  action: NonNullable<ActionState>
  catalog?: McpCatalog
  catalogLoading: boolean
  defaultParent: string
  liveIndex: LiveMountIndex
  onClose: () => void
  onComplete: (uri?: string) => Promise<void>
  rootUri: string
}) {
  const { t } = useTranslation('arranger')
  const [name, setName] = useState('')
  const [target, setTarget] = useState(
    action.kind === 'copy' || action.kind === 'move'
      ? joinVikingUri(defaultParent, action.source?.name || '')
      : defaultParent,
  )
  const [toolUri, setToolUri] = useState('')
  const [serverUri, setServerUri] = useState('')
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [timeout, setTimeoutValue] = useState('60')
  const [tags, setTags] = useState('')
  const [argumentValues, setArgumentValues] = useState<SchemaFormObject>({})
  const [argumentErrors, setArgumentErrors] = useState<RJSFValidationError[]>(
    [],
  )
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const initializedMountRef = useRef<string | null>(null)
  const initializedArgumentsRef = useRef<string | null>(null)
  const selectedTool = catalog?.tools.find((tool) => tool.uri === toolUri)
  const selectedServer = catalog?.servers.find(
    (server) => `${server.root_uri}/${server.name}` === serverUri,
  )
  const serverTools = selectedServer?.tools || []

  useEffect(() => {
    const source = action.source
    if (!source || initializedMountRef.current === source.uri) return
    const mount = liveIndex.mounts.get(source.uri)
    if (!mount) return
    initializedMountRef.current = source.uri
    setName(source.name)
    setToolUri(mount.tool_uri)
    setServerUri(parentVikingUri(mount.tool_uri))
    setTitle(mount.title || nameFromVikingUri(source.uri))
    setDescription(mount.description || '')
    setTimeoutValue(String(mount.timeout_seconds || 60))
    setTags((mount.tags || []).join(', '))
  }, [action.source, liveIndex.mounts])

  useEffect(() => {
    if (
      action.kind !== 'mount' ||
      action.source ||
      serverUri ||
      !catalog?.servers.length
    )
      return
    const server = catalog.servers[0]
    setServerUri(`${server.root_uri}/${server.name}`)
  }, [action.kind, action.source, catalog?.servers, serverUri])

  useEffect(() => {
    const source = action.source
    if (!source || !selectedTool) return
    const mount = liveIndex.mounts.get(source.uri)
    if (!mount || selectedTool.uri !== mount.tool_uri) return
    const initializationKey = `${source.uri}:${selectedTool.uri}`
    if (initializedArgumentsRef.current === initializationKey) return
    initializedArgumentsRef.current = initializationKey
    const values = initialSchemaArguments(
      selectedTool.tool.inputSchema,
      mount.arguments,
    )
    setArgumentValues(values)
  }, [action.source, liveIndex.mounts, selectedTool])

  const submit = async () => {
    setSubmitting(true)
    setError('')
    try {
      if (action.kind === 'mkdir') {
        const uri = joinVikingUri(defaultParent, name)
        await createDirectory(uri)
        await onComplete(uri)
        return
      }
      if (action.kind === 'file') {
        const uri = joinVikingUri(defaultParent, name)
        await createText(uri)
        await onComplete(uri)
        return
      }
      if (action.kind === 'mount') {
        if (!name.trim() || !toolUri || !title.trim()) {
          throw new Error(t('validation.mountRequired'))
        }
        if (!selectedTool) throw new Error(t('validation.selectTool'))
        const validated = validateSchemaArguments(
          selectedTool.tool.inputSchema,
          argumentValues,
        )
        setArgumentErrors(validated.errors)
        if (validated.errors.length) {
          throw new Error(t('validation.invalidArguments'))
        }
        const input: MountInput = {
          arguments: validated.arguments,
          description: description || undefined,
          node_uri: resolveMountNodeUri(
            defaultParent,
            name,
            action.source?.uri,
          ),
          tags: tags
            .split(',')
            .map((tag) => tag.trim())
            .filter(Boolean),
          timeout_seconds: timeout ? Number(timeout) : 60,
          title: title.trim(),
          tool_uri: toolUri,
        }
        if (
          !Number.isFinite(input.timeout_seconds) ||
          input.timeout_seconds <= 0
        ) {
          throw new Error(t('validation.invalidTimeout'))
        }
        await mountLiveContext(input)
        await onComplete(input.node_uri)
        return
      }
      // Internal invariant: every non-create action has a source resource.
      // eslint-disable-next-line studio-i18n/no-literal-message
      if (!action.source) throw new Error('Missing source resource')
      const mount = liveIndex.mounts.get(action.source.uri)
      if (mount) {
        const document = await readTextDocument(mount.node_uri)
        const metadata = parseLiveContextMetadata(document.content) || {}
        const completeMount = { ...mount, ...metadata }
        if (action.kind === 'copy') {
          await mountLiveContext({
            arguments: completeMount.arguments,
            description: completeMount.description,
            node_uri: normalizeVikingUri(target),
            tags: completeMount.tags,
            timeout_seconds: completeMount.timeout_seconds || 60,
            title:
              completeMount.title || nameFromVikingUri(completeMount.node_uri),
            tool_uri: completeMount.tool_uri,
          })
        } else {
          await moveLiveContext(completeMount, normalizeVikingUri(target))
        }
      } else {
        await mutateOrdinaryResource({
          kind: action.kind as MutationKind,
          liveIndex,
          rootUri,
          source: action.source,
          target: normalizeVikingUri(target),
        })
      }
      await onComplete(normalizeVikingUri(target))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className={cn(
          action.kind === 'mount' &&
            'h-[min(88vh,820px)] grid-rows-[auto_minmax(0,1fr)_auto] gap-0 overflow-hidden p-0 sm:max-w-3xl',
        )}
      >
        <DialogHeader className={cn(action.kind === 'mount' && 'px-6 py-5')}>
          <DialogTitle>{t(`dialogs.${action.kind}.title`)}</DialogTitle>
          <DialogDescription>
            {t(`dialogs.${action.kind}.description`)}
          </DialogDescription>
        </DialogHeader>
        <div
          className={cn(
            'space-y-4',
            action.kind === 'mount' &&
              'min-h-0 overflow-y-auto border-y px-6 py-5',
          )}
        >
          {(action.kind === 'mkdir' ||
            action.kind === 'file' ||
            action.kind === 'mount') && (
            <Field label={t('fields.name')} required>
              <Input
                value={name}
                readOnly={action.kind === 'mount' && Boolean(action.source)}
                onChange={(event) => setName(event.target.value)}
                placeholder={
                  action.kind === 'mount'
                    ? t('fields.mountNamePlaceholder')
                    : undefined
                }
              />
            </Field>
          )}
          {(action.kind === 'copy' || action.kind === 'move') && (
            <Field label={t('fields.targetUri')}>
              <Input
                value={target}
                onChange={(event) => setTarget(event.target.value)}
                className="font-mono text-xs"
              />
            </Field>
          )}
          {action.kind === 'mount' && (
            <>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label={t('fields.server')} required>
                  <Select
                    value={serverUri || null}
                    onValueChange={(value) => {
                      setServerUri(value === null ? '' : String(value))
                      setToolUri('')
                      setArgumentValues({})
                      setArgumentErrors([])
                    }}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue>
                        {selectedServer?.name ||
                          (catalogLoading
                            ? t('mount.loadingTools')
                            : t('mount.selectServer'))}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {(catalog?.servers || []).map((server) => (
                        <SelectItem
                          key={`${server.root_uri}/${server.name}`}
                          value={`${server.root_uri}/${server.name}`}
                        >
                          {server.name}
                          <span className="text-xs text-muted-foreground">
                            {server.root_uri}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {t('mount.toolCount', {
                              count: server.tools.length,
                            })}
                          </span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <Field label={t('fields.tool')} required>
                  <Select
                    value={toolUri || null}
                    disabled={!serverUri}
                    onValueChange={(value) => {
                      const uri = value === null ? '' : String(value)
                      const tool = catalog?.tools.find(
                        (item) => item.uri === uri,
                      )
                      setToolUri(uri)
                      setArgumentErrors([])
                      if (!tool) return
                      const defaults = initialSchemaArguments(
                        tool.tool.inputSchema,
                      )
                      setArgumentValues(defaults)
                    }}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue>
                        {selectedTool?.tool.title ||
                          selectedTool?.tool.name ||
                          t('mount.selectTool')}
                      </SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {serverTools.map((tool) => (
                        <SelectItem key={tool.uri} value={tool.uri}>
                          <span className="font-medium">
                            {tool.tool.title || tool.tool.name}
                          </span>
                          {tool.tool.title && (
                            <span className="font-mono text-xs text-muted-foreground">
                              {tool.tool.name}
                            </span>
                          )}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
              </div>

              {catalog?.error && (
                <Alert variant="destructive">
                  <AlertTitle>{t('mount.catalogIncomplete')}</AlertTitle>
                  <AlertDescription>{catalog.error}</AlertDescription>
                </Alert>
              )}

              {selectedTool && (
                <div className="rounded-md border bg-muted/20 px-3 py-2">
                  <p className="text-sm font-medium">
                    {selectedTool.tool.title || selectedTool.tool.name}
                  </p>
                  <p className="mt-1 text-xs leading-5 text-muted-foreground">
                    {selectedTool.tool.description || t('live.noDescription')}
                  </p>
                  <p className="mt-1 break-all font-mono text-[10px] text-muted-foreground">
                    {selectedTool.uri}
                  </p>
                </div>
              )}

              <div className="grid gap-4 sm:grid-cols-2">
                <Field label={t('fields.title')} required>
                  <Input
                    value={title}
                    onChange={(event) => setTitle(event.target.value)}
                  />
                </Field>
                <Field label={t('fields.timeout')} optional>
                  <Input
                    type="number"
                    min={1}
                    value={timeout}
                    onChange={(event) => setTimeoutValue(event.target.value)}
                  />
                </Field>
              </div>

              <div className="space-y-3 border-t pt-4">
                <div>
                  <h3 className="text-sm font-semibold">
                    {t('mount.argumentsTitle')}
                  </h3>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {t('mount.argumentsDescription')}
                  </p>
                </div>
                {selectedTool ? (
                  <SchemaArgumentsForm
                    schema={selectedTool.tool.inputSchema}
                    values={argumentValues}
                    errors={argumentErrors}
                    onChange={(value) => {
                      setArgumentValues(value)
                      setArgumentErrors([])
                    }}
                  />
                ) : (
                  <div className="rounded-md border border-dashed px-3 py-5 text-center text-sm text-muted-foreground">
                    {t('mount.chooseToolFirst')}
                  </div>
                )}
              </div>

              <div className="grid gap-4 border-t pt-4 sm:grid-cols-2">
                <div className="sm:col-span-2">
                  <Field label={t('fields.description')} optional>
                    <Textarea
                      value={description}
                      onChange={(event) => setDescription(event.target.value)}
                      className="min-h-20"
                    />
                  </Field>
                </div>
                <div className="sm:col-span-2">
                  <Field
                    label={t('fields.tags')}
                    optional
                    hint={t('fields.tagsHint')}
                  >
                    <Input
                      value={tags}
                      onChange={(event) => setTags(event.target.value)}
                      placeholder={t('fields.tagsPlaceholder')}
                    />
                  </Field>
                </div>
              </div>
            </>
          )}
          {error && <p className="text-sm text-destructive">{error}</p>}
        </div>
        <DialogFooter className={cn(action.kind === 'mount' && 'px-6 py-4')}>
          <Button variant="outline" onClick={onClose}>
            {t('actions.cancel')}
          </Button>
          <Button disabled={submitting} onClick={() => void submit()}>
            {submitting && <Loader2Icon className="animate-spin" />}
            {t('actions.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function Field({
  children,
  hint,
  label,
  optional,
  required,
}: {
  children: React.ReactNode
  hint?: string
  label: string
  optional?: boolean
  required?: boolean
}) {
  const { t } = useTranslation('arranger')
  return (
    <div className="min-w-0 space-y-1.5">
      <div className="flex items-center gap-2">
        <Label>{label}</Label>
        {(required || optional) && (
          <span
            className={cn(
              'text-[10px]',
              required
                ? 'font-medium text-destructive'
                : 'text-muted-foreground',
            )}
          >
            {required ? t('fields.required') : t('fields.optional')}
          </span>
        )}
      </div>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}
