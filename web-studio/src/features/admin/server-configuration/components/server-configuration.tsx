import { ModelConnectionTest } from '#/features/admin/server-configuration/components/model-connection-test'
import type {
  EditableModelKind,
  FieldSpec,
} from '#/features/admin/server-configuration/components/model-fields'
import {
  ModelFields,
  bindingFields,
  providers,
} from '#/features/admin/server-configuration/components/model-fields'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import type {
  ConfigFileConfiguration,
  ConfigFileDraft,
  ModelConfig,
} from '#/features/admin/server-configuration/lib/config-file-api'
import {
  createConfigFileApi,
  credentials,
  embeddingModes,
  isConfigFileObject,
  object,
  waitForServerRestart,
} from '#/features/admin/server-configuration/lib/config-file-api'
import { canEditStudioConfiguration } from '#/features/connection/lib/studio-permissions'
import { createRandomUuid } from '#/shared/lib/browser-crypto'
import { copyTextToClipboard } from '#/shared/lib/clipboard'
import { Badge } from '#/shared/ui/badge'
import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '#/shared/ui/dropdown-menu'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '#/shared/ui/tabs'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '#/shared/ui/tooltip'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangleIcon,
  ArrowDownIcon,
  ArrowUpIcon,
  CheckIcon,
  ClockIcon,
  CopyIcon,
  EyeIcon,
  FileTextIcon,
  GripVerticalIcon,
  LockIcon,
  MoreHorizontalIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  SaveIcon,
  Settings2Icon,
  Trash2Icon,
  Undo2Icon,
} from 'lucide-react'
import { useTheme } from 'next-themes'
import * as React from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

const LazyCodeEditor = React.lazy(() =>
  import('#/shared/components/code-editor').then((module) => ({
    default: module.CodeEditor,
  })),
)

const visibleModelKinds: ReadonlyArray<EditableModelKind> = ['vlm', 'embedding']

function Action({
  label,
  children,
  ...props
}: React.ComponentProps<typeof Button> & { label: string }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={label}
            {...props}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  )
}
type RowProps = {
  value: ModelConfig
  fallback: ModelConfig
  index: number
  count: number
  disabled: boolean
  onView: () => void
  onEdit: () => void
  onDelete: () => void
  onMove: (index: number) => void
  dragHandle: React.ReactNode
  dropProps: React.HTMLAttributes<HTMLDivElement>
  dropTarget?: 'before' | 'after'
}
function ModelRow({
  value,
  fallback,
  index,
  count,
  disabled,
  onView,
  onEdit,
  onDelete,
  onMove,
  dragHandle,
  dropProps,
  dropTarget,
}: RowProps) {
  const { t } = useTranslation('settings')
  const ordered = count > 1
  const modelId = String(
    value.model || value.model_name || fallback.model || t('models.notSet'),
  )
  return (
    <div
      {...dropProps}
      data-model-row=""
      data-drop-target={dropTarget}
      className={`grid min-w-0 items-start gap-2 border-b bg-background py-3 data-[drop-target]:bg-accent data-[drop-target=before]:shadow-[inset_0_2px_0_var(--primary)] data-[drop-target=after]:shadow-[inset_0_-2px_0_var(--primary)] text-sm md:items-center ${ordered ? 'grid-cols-[56px_minmax(0,1fr)] md:grid-cols-[56px_minmax(0,1.4fr)_100px_minmax(0,1.4fr)_80px_104px]' : 'grid-cols-[0px_minmax(0,1fr)] gap-x-0 md:grid-cols-[minmax(0,1.4fr)_100px_minmax(0,1.4fr)_80px_104px] md:gap-x-2'}`}
    >
      <div
        className={
          ordered
            ? 'flex items-center gap-1 text-xs text-muted-foreground'
            : 'md:hidden'
        }
      >
        {ordered && dragHandle}
        {ordered ? index + 1 : null}
      </div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onView}
            className="max-w-full break-all text-left font-mono text-sm font-medium hover:underline"
          >
            {modelId}
          </button>
          <Badge
            variant={index === 0 ? 'secondary' : 'outline'}
            title={t('models.priorityHint')}
          >
            {t(index === 0 ? 'models.preferred' : 'models.backup')}
          </Badge>
        </div>
        <span className="mt-1 block text-xs text-muted-foreground md:hidden">
          {String(value.provider || '')}
        </span>
      </div>
      <span className="hidden break-all md:block">
        {String(value.provider || '')}
      </span>
      <span className="col-start-2 row-start-2 min-w-0 break-all text-xs text-muted-foreground md:col-auto md:row-auto">
        {String(value.api_base || value.host || t('models.providerDefault'))}
      </span>
      <span className="col-start-2 row-start-3 font-mono text-xs text-muted-foreground md:col-auto md:row-auto">
        {value.api_key || value.sk || value.ak
          ? '********'
          : t('models.notSet')}
      </span>
      <div className="col-start-2 row-start-4 flex justify-end [&_button]:size-10 md:col-auto md:row-auto md:[&_button]:size-8">
        <Action label={t('models.view')} onClick={onView}>
          <EyeIcon />
        </Action>
        <Action label={t('models.edit')} disabled={disabled} onClick={onEdit}>
          <PencilIcon />
        </Action>
        <DropdownMenu>
          <DropdownMenuTrigger
            render={
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={t('models.more')}
                disabled={disabled}
              />
            }
          >
            <MoreHorizontalIcon />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="min-w-40">
            <DropdownMenuItem
              onClick={() =>
                void copyTextToClipboard(modelId)
                  .then(() => toast.success(t('models.copied')))
                  .catch(() => toast.error(t('models.copyFailed')))
              }
            >
              <CopyIcon />
              {t('models.copyModelId')}
            </DropdownMenuItem>
            {count > 1 && (
              <>
                <DropdownMenuItem
                  disabled={index === 0}
                  onClick={() => onMove(index - 1)}
                >
                  <ArrowUpIcon />
                  {t('models.moveUp')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  disabled={index === count - 1}
                  onClick={() => onMove(index + 1)}
                >
                  <ArrowDownIcon />
                  {t('models.moveDown')}
                </DropdownMenuItem>
              </>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem
              variant="destructive"
              disabled={count === 1}
              onClick={onDelete}
            >
              <Trash2Icon />
              {t('models.remove')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}
function ModelList({
  group,
  values,
  fallback,
  disabled,
  onOrder,
  onOpen,
  onDelete,
}: {
  group: string
  values: ModelConfig[]
  fallback: ModelConfig
  disabled: boolean
  onOrder: (values: ModelConfig[]) => void
  onOpen: (index: number, readonly: boolean) => void
  onDelete: (index: number) => void
}) {
  const { t } = useTranslation('settings')
  const [drag, setDrag] = React.useState<{ from: number; to: number } | null>(
    null,
  )
  const ids = values.map((value, index) => `${group}-${value.id || index}`)
  function move(from: number, to: number) {
    if (disabled || from < 0 || to < 0 || to >= values.length || from === to)
      return
    const reordered = [...values]
    const [moved] = reordered.splice(from, 1)
    reordered.splice(to, 0, moved)
    onOrder(reordered)
  }
  if (!values.length)
    return (
      <p className="py-4 text-sm text-muted-foreground">{t('models.empty')}</p>
    )
  return (
    <div className="min-w-0">
      <div
        className={`hidden gap-2 border-b pb-2 text-xs text-muted-foreground md:grid ${values.length > 1 ? 'grid-cols-[56px_minmax(0,1.4fr)_100px_minmax(0,1.4fr)_80px_104px]' : 'grid-cols-[minmax(0,1.4fr)_100px_minmax(0,1.4fr)_80px_104px]'}`}
      >
        {values.length > 1 && <span>{t('models.priority')}</span>}
        <span>{t('models.listModel')}</span>
        <span>{t('models.provider')}</span>
        <span>{t('models.apiBase')}</span>
        <span>{t('models.apiKey')}</span>
        <span className="text-right">{t('models.actions')}</span>
      </div>
      {values.map((value, index) => (
        <ModelRow
          key={ids[index]}
          dropProps={{
            draggable: !disabled && values.length > 1,
            onDragStart: (event) => {
              const control = (event.target as Element).closest(
                'button, a, input, select, textarea',
              )
              if (
                disabled ||
                (control && !control.hasAttribute('data-model-drag-handle'))
              ) {
                event.preventDefault()
                return
              }
              const row = event.currentTarget
              const bounds = row.getBoundingClientRect()
              event.dataTransfer.effectAllowed = 'move'
              event.dataTransfer.setData('text/plain', ids[index])
              event.dataTransfer.setDragImage(
                row,
                event.clientX - bounds.left,
                event.clientY - bounds.top,
              )
              setDrag({ from: index, to: index })
            },
            onDragEnd: () => setDrag(null),
            onDragOver: (event) => {
              if (disabled || !drag) return
              event.preventDefault()
              event.dataTransfer.dropEffect = 'move'
              if (drag.to !== index) setDrag({ ...drag, to: index })
            },
            onDrop: (event) => {
              if (disabled || !drag) return
              event.preventDefault()
              move(drag.from, index)
              setDrag(null)
            },
          }}
          dropTarget={
            !disabled && drag?.to === index && drag.from !== index
              ? drag.from > index
                ? 'before'
                : 'after'
              : undefined
          }
          dragHandle={
            <button
              type="button"
              data-model-drag-handle=""
              disabled={disabled}
              aria-label={t('models.dragToReorder', {
                model: String(value.model || fallback.model || index + 1),
              })}
              title={t('models.dragHint')}
              className="flex size-6 shrink-0 cursor-grab items-center justify-center rounded hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring active:cursor-grabbing disabled:cursor-default"
              onKeyDown={(event) => {
                if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return
                event.preventDefault()
                move(index, index + (event.key === 'ArrowUp' ? -1 : 1))
              }}
            >
              <GripVerticalIcon className="size-4" />
            </button>
          }
          value={value}
          fallback={fallback}
          index={index}
          count={values.length}
          disabled={disabled}
          onView={() => onOpen(index, true)}
          onEdit={() => onOpen(index, false)}
          onDelete={() => onDelete(index)}
          onMove={(to) => move(index, to)}
        />
      ))}
    </div>
  )
}
type Editor = {
  kind: EditableModelKind
  mode?: string
  index?: number
  value: ModelConfig
  readonly: boolean
  settings: boolean
  thinking?: boolean
}
const titles: Record<EditableModelKind, string> = {
  vlm: 'models.vlmType',
  embedding: 'models.embeddingType',
}
const policies: FieldSpec[] = [
  { key: 'max_retries', type: 'number', min: 0 },
  { key: 'max_concurrent', type: 'number', min: 1 },
  { key: 'circuit_breaker', type: 'json' },
]

export function ServerConfigurationEditor() {
  const { t } = useTranslation('settings')
  const { resolvedTheme } = useTheme()
  const {
    connection,
    connectionRole,
    serverMode,
    identityScopeKey,
    isConnectionRoleLoading,
  } = useAppConnection()
  const service = useStudioService()
  const allowed =
    service.ready &&
    canEditStudioConfiguration({
      serviceProvider: service.provider,
      hasControlCredential: Boolean(connection.adminApiKey.trim()),
      isRoleLoading: isConnectionRoleLoading,
      role: connectionRole,
      serverMode,
    })
  function requireAccess() {
    if (!allowed) throw new Error(t('models.rootRequired'))
  }
  const api = React.useMemo(
    () => createConfigFileApi(connection, serverMode === 'trusted'),
    [connection, serverMode],
  )
  const queryClient = useQueryClient()
  const queryKey = ['server-configuration', identityScopeKey]
  const query = useQuery({
    queryKey,
    queryFn: () => {
      requireAccess()
      return api.get()
    },
    enabled: allowed,
    retry: false,
  })
  const [draft, setDraft] = React.useState<ConfigFileDraft | null>(null)
  const [editMode, setEditMode] = React.useState<'form' | 'file'>('form')
  const [baseline, setBaseline] =
    React.useState<ConfigFileConfiguration | null>(null)
  const [editor, setEditor] = React.useState<Editor | null>(null)
  const [confirm, setConfirm] = React.useState<{
    kind: EditableModelKind
    mode?: string
    index: number
  } | null>(null)
  const [saved, setSaved] = React.useState(false)
  const restartAbort = React.useRef<AbortController | null>(null)
  React.useEffect(() => () => restartAbort.current?.abort(), [api])
  const restartMutation = useMutation({
    mutationFn: async (revision: string) => {
      requireAccess()
      const controller = new AbortController()
      restartAbort.current = controller
      const accepted = await api.restart(revision)
      return waitForServerRestart(
        () => api.get(3000),
        accepted.instance_id,
        controller.signal,
      )
    },
    onSuccess: (result) => {
      queryClient.setQueryData(queryKey, result)
      setSaved(false)
      if (result.restart?.rolled_back)
        toast.error(t('models.restartRolledBack'))
      else toast.success(t('models.restarted'))
    },
  })
  const mutation = useMutation({
    mutationFn: ({ content }: { content: string; restart?: boolean }) => {
      requireAccess()
      return api.save(content, (baseline ?? query.data)!.revision)
    },
    onSuccess: async (result, variables) => {
      queryClient.setQueryData<ConfigFileConfiguration>(queryKey, (previous) =>
        previous
          ? {
              ...previous,
              ...result,
              content: variables.content,
              models: document?.models ?? previous.models,
            }
          : previous,
      )
      await queryClient.invalidateQueries({ queryKey })
      setDraft(null)
      setEditor(null)
      setBaseline(null)
      setSaved(true)
      toast.success(t('models.saved'))
      if (variables.restart) restartMutation.mutate(result.revision)
    },
  })
  const document = draft ?? baseline ?? query.data
  const dirty =
    draft !== null && draft.content !== (baseline ?? query.data)?.content
  const preview = useMutation({
    mutationFn: ({
      content,
      changes,
    }: {
      content: string
      changes?: Partial<Record<EditableModelKind, ModelConfig>>
      returnToForm?: boolean
    }) => {
      requireAccess()
      return api.preview(content, changes)
    },
    onSuccess: (result, variables) => {
      setDraft(result)
      setEditor(null)
      setConfirm(null)
      if (variables.returnToForm) setEditMode('form')
    },
  })
  const pending =
    mutation.isPending || preview.isPending || restartMutation.isPending
  const invalidJson = !isConfigFileObject(document?.content ?? '{}')
  function current(kind: EditableModelKind): ModelConfig {
    return document?.models[kind].config ?? {}
  }
  function change(kind: EditableModelKind, value: ModelConfig) {
    if (pending || !document) return
    setBaseline((previous) => previous ?? query.data ?? null)
    mutation.reset()
    setSaved(false)
    preview.mutate({ content: document.content, changes: { [kind]: value } })
  }
  function bindings(kind: EditableModelKind, mode?: string) {
    const config = current(kind)
    return credentials(mode ? object(config[mode]) : config)
  }
  function reorder(
    kind: EditableModelKind,
    mode: string | undefined,
    values: ModelConfig[],
    shared: ModelConfig = {},
  ) {
    const config = { ...current(kind), ...shared }
    change(
      kind,
      mode
        ? {
            ...config,
            [mode]: { ...object(config[mode]), credentials: values },
          }
        : { ...config, credentials: values },
    )
  }
  function open(
    kind: EditableModelKind,
    mode?: string,
    index?: number,
    readonly = false,
    settings = false,
  ) {
    if (pending && !readonly) return
    setBaseline((previous) => previous ?? query.data ?? null)
    const config = current(kind)
    const value = settings
      ? config
      : index === undefined
        ? {
            provider: providers[kind][0],
            id: createRandomUuid(),
          }
        : bindings(kind, mode)[index]
    setEditor({
      kind,
      mode,
      index,
      readonly,
      settings,
      value: structuredClone(value),
      thinking:
        kind === 'vlm' && !settings ? config.thinking === true : undefined,
    })
  }
  function dismissEditor() {
    setEditor(null)
    if (!dirty) {
      setDraft(null)
      setBaseline(null)
    }
  }
  function applyEditor() {
    if (!editor || editor.readonly || pending) return
    const { kind, mode, index, value, settings } = editor
    if (settings) change(kind, value)
    else {
      const values = [...bindings(kind, mode)]
      const next = Object.fromEntries(
        Object.entries(value).map(([key, item]) => [
          key,
          item === '' ? null : item,
        ]),
      )
      if (index === undefined) values.push(next)
      else values[index] = next
      const config = current(kind)
      const shared =
        kind === 'vlm' && editor.thinking !== (config.thinking === true)
          ? { thinking: editor.thinking }
          : {}
      if (!mode && !config.model)
        change(kind, {
          ...config,
          ...shared,
          model: String(next.model || ''),
          credentials: values,
        })
      else reorder(kind, mode, values, shared)
    }
  }
  function dismissConfirmation() {
    if (pending) return
    setConfirm(null)
    if (!dirty) {
      setDraft(null)
      setBaseline(null)
    }
  }
  function confirmAction() {
    if (!confirm || pending) return
    reorder(
      confirm.kind,
      confirm.mode,
      bindings(confirm.kind, confirm.mode).filter(
        (_, index) => index !== confirm.index,
      ),
    )
  }
  if (isConnectionRoleLoading || (allowed && query.isPending))
    return <p role="status">{t('models.loading')}</p>
  if (!allowed)
    return (
      <p role="status" className="text-muted-foreground">
        {t('models.rootRequired')}
      </p>
    )
  const loadError = query.isError ? (
    <div role="alert" className="grid gap-3">
      <p>{t('models.loadFailed')}</p>
      {query.error instanceof Error && (
        <p className="break-all text-sm text-muted-foreground">
          {query.error.message}
        </p>
      )}
      <Button
        variant="outline"
        disabled={pending}
        onClick={() => void query.refetch()}
      >
        {t('models.retry')}
      </Button>
    </div>
  ) : null
  if (query.isError && !query.data) return loadError
  const hasOverrides = Boolean(
    query.data?.overrides?.cluster.length ||
    query.data?.overrides?.account.length,
  )
  const provider = String(editor?.value.provider || '').toLowerCase()
  const fields = editor?.settings
    ? editor.kind === 'embedding'
      ? policies
      : ([
          { key: 'model', required: true },
          { key: 'timeout', type: 'number', min: 0.001 },
          { key: 'thinking', type: 'toggle' },
        ] as FieldSpec[])
    : editor
      ? bindingFields(editor.kind, provider).map((field) =>
          field.key === 'model' &&
          editor.kind === 'vlm' &&
          !current(editor.kind).model
            ? { ...field, required: true }
            : field,
        )
      : []
  return (
    <TooltipProvider>
      <div className="grid min-w-0 gap-8">
        <div className="grid gap-3 border-b pb-5 text-sm">
          {loadError}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <FileTextIcon className="size-4 text-muted-foreground" />
              <span className="font-medium">{t('models.serverSource')}</span>
              <Badge
                variant="outline"
                className={
                  query.data?.restart_required || saved || hasOverrides
                    ? 'border-amber-500/40 text-amber-700 dark:text-amber-300'
                    : ''
                }
              >
                {query.data?.writable === false ? (
                  <LockIcon />
                ) : hasOverrides ? (
                  <AlertTriangleIcon />
                ) : query.data?.restart_required || saved ? (
                  <ClockIcon />
                ) : (
                  <CheckIcon />
                )}
                {t(
                  query.data?.writable === false
                    ? 'models.readOnlyStatus'
                    : hasOverrides
                      ? 'models.hasOverrides'
                      : query.data?.restart_required || saved
                        ? 'models.pendingRestart'
                        : 'models.inSync',
                )}
              </Badge>
            </div>
            <Action
              label={t('models.reloadFile')}
              disabled={pending || query.isFetching || dirty}
              onClick={() => {
                setSaved(false)
                mutation.reset()
                preview.reset()
                setDraft(null)
                setBaseline(null)
                void query.refetch()
              }}
            >
              <RefreshCwIcon />
            </Action>
          </div>
          <details className="text-xs text-muted-foreground">
            <summary className="w-fit cursor-pointer hover:text-foreground">
              {t('models.fileLocation')}
            </summary>
            <p className="mt-2 break-all font-mono">{query.data?.file_path}</p>
          </details>
          {(query.data?.restart_required || saved) && (
            <p role="status" className="text-amber-700 dark:text-amber-300">
              {t('models.restartRequired')}
            </p>
          )}
          {restartMutation.isPending && (
            <p role="status">{t('models.restarting')}</p>
          )}
          {restartMutation.isError && (
            <p role="alert" className="text-destructive">
              {t('models.restartFailed')}{' '}
              {restartMutation.error instanceof Error
                ? restartMutation.error.message
                : ''}
            </p>
          )}
          {query.data?.restart?.rolled_back && (
            <p role="alert" className="text-destructive">
              {t('models.restartRolledBack')}
            </p>
          )}
          {query.data?.restart?.supported === false && (
            <p className="text-sm text-muted-foreground">
              {t('models.restartUnsupported')}
            </p>
          )}
          {(query.data?.restart_required || saved) &&
            query.data?.restart?.supported &&
            !dirty && (
              <Button
                type="button"
                variant="outline"
                disabled={pending}
                onClick={() => restartMutation.mutate(query.data.revision)}
              >
                {t('models.restartService')}
              </Button>
            )}
          {query.data?.writable === false && (
            <p role="alert">{t('models.fileReadOnly')}</p>
          )}
          {saved && (
            <p role="status" className="text-muted-foreground">
              {t('models.saved')}
            </p>
          )}
          {mutation.isError && (
            <p role="alert" className="text-destructive">
              {t('models.saveFailed')}{' '}
              {mutation.error instanceof Error ? mutation.error.message : ''}
            </p>
          )}
          {Boolean(
            query.data?.overrides?.cluster.length ||
            query.data?.overrides?.account.length,
          ) && (
            <p role="alert" className="text-destructive">
              {t('models.overrideWarning')}{' '}
              {[
                ...(query.data?.overrides?.cluster || []).map(
                  (key) => `cluster.${key}`,
                ),
                ...(query.data?.overrides?.account || []).map(
                  (key) => `${connection.accountId}.${key}`,
                ),
              ].join(', ')}
            </p>
          )}
        </div>
        <Tabs
          value={editMode}
          className="min-w-0 gap-6"
          onValueChange={(value) => {
            if (pending || value === editMode) return
            if (value === 'file') setEditMode('file')
            else if (value === 'form' && document && !invalidJson) {
              setBaseline((previous) => previous ?? query.data ?? null)
              preview.mutate({ content: document.content, returnToForm: true })
            }
          }}
        >
          <TabsList variant="line" aria-label={t('models.editMode')}>
            <TabsTrigger value="form" disabled={pending || invalidJson}>
              {t('models.formMode')}
            </TabsTrigger>
            <TabsTrigger value="file" disabled={pending}>
              {t('models.fileMode')}
            </TabsTrigger>
          </TabsList>
          <p className="text-sm text-muted-foreground">
            {t('models.fileScope')}
          </p>
          {preview.isError && !editor && !confirm && (
            <p role="alert" className="break-all text-destructive">
              {t('models.validateFailed')} {preview.error.message}
            </p>
          )}
          <TabsContent value="file" className="min-w-0">
            <div className="grid gap-3">
              <p className="text-sm font-medium">{t('models.fileContent')}</p>
              <div className="h-[min(60vh,36rem)] min-h-80 min-w-0">
                <React.Suspense
                  fallback={<p role="status">{t('models.loading')}</p>}
                >
                  <LazyCodeEditor
                    filename="ov.conf"
                    language="json"
                    ariaLabel={t('models.fileContent')}
                    initialContent={document?.content ?? ''}
                    isDark={resolvedTheme === 'dark'}
                    lineWrapping
                    readOnly={pending || query.data?.writable === false}
                    onChange={(content) => {
                      if (pending || !document) return
                      setBaseline((previous) => previous ?? query.data ?? null)
                      setSaved(false)
                      mutation.reset()
                      preview.reset()
                      setDraft({ ...document, content })
                    }}
                  />
                </React.Suspense>
              </div>
              {invalidJson && (
                <p role="alert" className="text-destructive">
                  {t('models.invalidJsonObject')}
                </p>
              )}
            </div>
          </TabsContent>
          <TabsContent value="form" className="grid min-w-0 gap-8">
            {visibleModelKinds.map((kind) => {
              if (document?.form_readonly)
                return (
                  <section key={kind} aria-label={t(titles[kind])}>
                    <h2 className="mb-4 text-base font-semibold">
                      {t(titles[kind])}
                    </h2>
                    <p className="text-sm text-muted-foreground">
                      {t('models.environmentObject')}
                    </p>
                  </section>
                )
              const config = current(kind)
              const groups =
                kind === 'embedding'
                  ? embeddingModes
                      .filter((mode) => config[mode])
                      .map((mode) => ({ mode, config: object(config[mode]) }))
                  : [{ mode: undefined, config }]
              return (
                <section
                  key={kind}
                  aria-label={t(titles[kind])}
                  className="min-w-0 border-b pb-6 last:border-b-0"
                >
                  <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-base font-semibold">
                        {t(titles[kind])}
                      </h2>
                      {dirty && (
                        <Badge variant="secondary">{t('models.unsaved')}</Badge>
                      )}
                    </div>
                    <div className="flex items-center gap-1">
                      <Action
                        label={t('models.parameters')}
                        disabled={pending}
                        onClick={() =>
                          open(kind, undefined, undefined, false, true)
                        }
                      >
                        <Settings2Icon />
                      </Action>
                      {kind === 'embedding' && groups.length > 1 ? (
                        <DropdownMenu>
                          <DropdownMenuTrigger
                            render={
                              <Button
                                type="button"
                                variant="ghost"
                                size="icon-sm"
                                aria-label={t('models.addModel')}
                                title={t('models.addModel')}
                                disabled={pending}
                              />
                            }
                          >
                            <PlusIcon />
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            {groups.map((group) => (
                              <DropdownMenuItem
                                key={group.mode}
                                onClick={() => open(kind, group.mode)}
                              >
                                {t(`models.${group.mode}`)}
                              </DropdownMenuItem>
                            ))}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      ) : (
                        <Action
                          label={t('models.addModel')}
                          disabled={
                            pending || (kind === 'embedding' && !groups.length)
                          }
                          onClick={() =>
                            open(
                              kind,
                              kind === 'embedding'
                                ? groups[0]?.mode
                                : undefined,
                            )
                          }
                        >
                          <PlusIcon />
                        </Action>
                      )}
                    </div>
                  </div>
                  {groups.map((group) => (
                    <div key={group.mode || kind} className="min-w-0">
                      {group.mode && (
                        <div className="mb-3 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                          <Badge variant="outline">
                            {t(`models.${group.mode}`)}
                          </Badge>
                          <span>
                            {t('models.dimension')}:{' '}
                            {String(group.config.dimension)}
                          </span>
                          {credentials(group.config).some(
                            (binding) =>
                              binding.model &&
                              binding.model !== group.config.model,
                          ) && (
                            <span className="break-all">
                              {t('models.model')}: {String(group.config.model)}
                            </span>
                          )}
                        </div>
                      )}
                      <ModelList
                        group={`${kind}-${group.mode || ''}`}
                        values={credentials(group.config)}
                        fallback={group.config}
                        disabled={pending}
                        onOrder={(values) => reorder(kind, group.mode, values)}
                        onOpen={(index, readonly) =>
                          open(kind, group.mode, index, readonly)
                        }
                        onDelete={(index) => {
                          setBaseline(
                            (previous) => previous ?? query.data ?? null,
                          )
                          setConfirm({ kind, mode: group.mode, index })
                        }}
                      />
                    </div>
                  ))}
                </section>
              )
            })}
          </TabsContent>
        </Tabs>
        {dirty && (
          <div className="sticky bottom-4 z-20 flex flex-wrap items-center justify-between gap-3 rounded-md border bg-background/95 p-3 shadow-lg backdrop-blur-sm">
            <p className="text-sm">{t('models.unsaved')}</p>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={pending}
                onClick={() => {
                  setDraft(null)
                  setBaseline(null)
                  preview.reset()
                  mutation.reset()
                  setSaved(false)
                }}
              >
                <Undo2Icon />
                {t('models.discardAll')}
              </Button>
              <Button
                type="button"
                size="sm"
                disabled={
                  pending || invalidJson || query.data?.writable === false
                }
                onClick={() =>
                  document && mutation.mutate({ content: document.content })
                }
              >
                <SaveIcon />
                {t(
                  mutation.isPending
                    ? 'models.saving'
                    : preview.isPending
                      ? 'models.validating'
                      : 'models.saveAll',
                )}
              </Button>
              {query.data?.restart?.supported && (
                <Button
                  type="button"
                  size="sm"
                  disabled={
                    pending || invalidJson || query.data.writable === false
                  }
                  onClick={() =>
                    document &&
                    mutation.mutate({
                      content: document.content,
                      restart: true,
                    })
                  }
                >
                  {t('models.saveAndRestart')}
                </Button>
              )}
            </div>
          </div>
        )}
      </div>
      <Dialog
        open={Boolean(editor)}
        onOpenChange={(isOpen) => {
          if (!isOpen && !pending) dismissEditor()
        }}
      >
        <DialogContent className="gap-0 overflow-hidden rounded-lg p-0 sm:max-w-lg">
          <DialogHeader className="border-b px-6 py-5 pr-14">
            <DialogTitle className="text-base">
              {editor ? t(titles[editor.kind]) : ''} ·{' '}
              {t(
                editor?.readonly
                  ? 'models.view'
                  : editor?.settings
                    ? 'models.parameters'
                    : 'models.edit',
              )}
            </DialogTitle>
          </DialogHeader>
          {editor && (
            <form
              className="flex min-h-0 flex-col"
              onSubmit={(event) => {
                event.preventDefault()
                applyEditor()
              }}
            >
              <div className="grid max-h-[calc(100dvh-14rem)] gap-5 overflow-y-auto px-6 py-5">
                {preview.isError && (
                  <p
                    role="alert"
                    className="break-all text-sm text-destructive"
                  >
                    {t('models.validateFailed')} {preview.error.message}
                  </p>
                )}
                <ModelFields
                  key={`${editor.kind}-${editor.mode}-${editor.index}-${editor.settings}`}
                  fields={fields}
                  value={editor.value}
                  inheritedModel={
                    editor.settings
                      ? undefined
                      : (editor.mode
                          ? object(current(editor.kind)[editor.mode])
                          : current(editor.kind)
                        ).model
                  }
                  readOnly={editor.readonly || pending}
                  onChange={(value) => {
                    if (pending) return
                    if (value.provider !== editor.value.provider) {
                      for (const key of [
                        'api_key',
                        'ak',
                        'sk',
                        'api_base',
                        'api_version',
                        'host',
                        'region',
                        'extra_headers',
                        'extra_request_body',
                      ]) {
                        value[key] = null
                      }
                    }
                    setEditor({ ...editor, value })
                  }}
                />
                {editor.kind === 'vlm' && !editor.settings && (
                  <div className="grid gap-2 border-t pt-4">
                    <ModelFields
                      prefix="shared-vlm"
                      fields={[{ key: 'thinking', type: 'toggle' }]}
                      value={{ thinking: editor.thinking }}
                      readOnly={editor.readonly || pending}
                      onChange={(value) => {
                        if (!pending)
                          setEditor({
                            ...editor,
                            thinking: value.thinking === true,
                          })
                      }}
                    />
                    <p className="text-xs text-muted-foreground">
                      {t('models.thinkingHint')}
                    </p>
                  </div>
                )}
                {!editor.settings && (
                  <ModelConnectionTest
                    mode={editor.mode}
                    kind={editor.kind}
                    config={editor.value}
                    fallback={
                      editor.mode
                        ? object(current(editor.kind)[editor.mode])
                        : current(editor.kind)
                    }
                    thinking={editor.thinking}
                    disabled={pending}
                  />
                )}
                {editor.kind === 'embedding' && editor.settings && (
                  <>
                    <p className="text-xs text-muted-foreground">
                      {t('models.embeddingContract')}
                    </p>
                    <ModelFields
                      fields={[
                        { key: 'text_source' },
                        { key: 'max_input_tokens', type: 'number' },
                      ]}
                      value={editor.value}
                      readOnly
                      onChange={() => {}}
                      prefix="embedding-contract"
                    />
                    {embeddingModes
                      .filter((mode) => editor.value[mode])
                      .map((mode) => (
                        <fieldset
                          key={mode}
                          className="grid gap-4 border-t pt-4"
                        >
                          <legend className="text-sm">
                            {t(`models.${mode}`)}
                          </legend>
                          <ModelFields
                            prefix={`${mode}-contract`}
                            fields={[
                              { key: 'model' },
                              { key: 'dimension', type: 'number' },
                              { key: 'input' },
                              { key: 'query_param' },
                              { key: 'document_param' },
                              { key: 'version' },
                            ]}
                            value={object(editor.value[mode])}
                            readOnly
                            onChange={() => {}}
                          />
                          <ModelFields
                            prefix={mode}
                            fields={[
                              {
                                key: 'failback_timeout_seconds',
                                type: 'number',
                                min: 0.001,
                              },
                              {
                                key: 'failback_request_count',
                                type: 'number',
                                min: 1,
                              },
                            ]}
                            value={object(editor.value[mode])}
                            readOnly={pending}
                            onChange={(value) => {
                              if (pending) return
                              setEditor({
                                ...editor,
                                value: { ...editor.value, [mode]: value },
                              })
                            }}
                          />
                        </fieldset>
                      ))}
                  </>
                )}
                {!editor.settings && editor.kind === 'embedding' && (
                  <details>
                    <summary className="cursor-pointer text-sm text-muted-foreground">
                      {t('models.advanced')}
                    </summary>
                    <div className="mt-4 grid gap-4">
                      <ModelFields
                        key={provider}
                        fields={[{ key: 'extra_headers', type: 'json' }]}
                        value={editor.value}
                        readOnly={editor.readonly || pending}
                        onChange={(value) => {
                          if (!pending) setEditor({ ...editor, value })
                        }}
                      />
                    </div>
                  </details>
                )}
              </div>
              <DialogFooter className="flex-row justify-end border-t bg-muted/20 px-6 py-4">
                <Button
                  type="button"
                  variant="outline"
                  disabled={pending}
                  onClick={dismissEditor}
                >
                  {t(editor.readonly ? 'models.close' : 'models.dismiss')}
                </Button>
                {editor.readonly ? (
                  <Button
                    type="button"
                    disabled={pending}
                    onClick={(event) => {
                      event.preventDefault()
                      if (pending) return
                      setBaseline((previous) => previous ?? query.data ?? null)
                      setEditor({ ...editor, readonly: false })
                    }}
                  >
                    <PencilIcon />
                    {t('models.edit')}
                  </Button>
                ) : (
                  <Button type="submit" disabled={pending}>
                    {t('models.apply')}
                  </Button>
                )}
              </DialogFooter>
            </form>
          )}
        </DialogContent>
      </Dialog>
      <Dialog
        open={Boolean(confirm)}
        onOpenChange={(isOpen) => {
          if (!isOpen) dismissConfirmation()
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('models.confirmRemove')}</DialogTitle>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={pending}
              onClick={dismissConfirmation}
            >
              {t('models.dismiss')}
            </Button>
            <Button
              variant="destructive"
              disabled={pending}
              onClick={confirmAction}
            >
              {t('models.remove')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </TooltipProvider>
  )
}
