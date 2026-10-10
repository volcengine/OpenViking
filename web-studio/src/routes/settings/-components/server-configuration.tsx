import * as React from 'react'
import { toast } from 'sonner'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  DndContext,
  PointerSensor,
  KeyboardSensor,
  closestCenter,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  SortableContext,
  useSortable,
  arrayMove,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import {
  ArrowUpIcon,
  ArrowDownIcon,
  EyeIcon,
  GripVerticalIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  MoreHorizontalIcon,
  FileTextIcon,
  CheckIcon,
  ClockIcon,
  LockIcon,
  AlertTriangleIcon,
  CopyIcon,
  SaveIcon,
  Settings2Icon,
  Trash2Icon,
  Undo2Icon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAppConnection } from '#/hooks/use-app-connection'
import { createRandomUuid } from '#/lib/browser-crypto'
import { copyTextToClipboard } from '#/lib/clipboard'
import { Button } from '#/components/ui/button'
import { Textarea } from '#/components/ui/textarea'
import { Badge } from '#/components/ui/badge'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from '#/components/ui/dropdown-menu'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '#/components/ui/dialog'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '#/components/ui/tooltip'
import {
  ModelFields,
  bindingFields,
  advancedFields,
  providers,
} from './model-fields'
import {
  createConfigFileApi,
  embeddingModes,
  credentials,
  object,
  isConfigFileObject,
} from '../-lib/config-file-api'
import type {
  ConfigFileDraft,
  ConfigFileConfiguration,
  ModelConfig,
} from '../-lib/config-file-api'
import type { EditableModelKind, FieldSpec } from './model-fields'

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
  id: string
  value: ModelConfig
  fallback: ModelConfig
  index: number
  count: number
  disabled: boolean
  onView: () => void
  onEdit: () => void
  onDelete: () => void
  onMove: (index: number) => void
  sortable: boolean
}
function ModelRow({
  id,
  value,
  fallback,
  index,
  count,
  disabled,
  onView,
  onEdit,
  onDelete,
  onMove,
  sortable,
}: RowProps) {
  const { t } = useTranslation('settings')
  const drag = useSortable({ id, disabled: disabled || !sortable })
  const ordered = sortable && count > 1
  const modelId = String(
    value.model || value.model_name || fallback.model || t('models.notSet'),
  )
  return (
    <div
      ref={drag.setNodeRef}
      style={{
        transform: CSS.Transform.toString(drag.transform),
        transition: drag.transition,
      }}
      className={`grid min-w-0 items-start gap-2 border-b bg-background py-3 text-sm md:items-center ${ordered ? 'grid-cols-[24px_minmax(0,1fr)] md:grid-cols-[24px_32px_minmax(0,1.4fr)_100px_minmax(0,1.4fr)_80px_104px]' : 'grid-cols-[0px_minmax(0,1fr)] gap-x-0 md:grid-cols-[minmax(0,1.4fr)_100px_minmax(0,1.4fr)_80px_104px] md:gap-x-2'} ${drag.isDragging ? 'relative z-10 shadow-md' : ''}`}
    >
      {sortable && count > 1 ? (
        <Action
          label={t('models.drag')}
          disabled={disabled}
          ref={drag.setActivatorNodeRef}
          {...drag.attributes}
          {...drag.listeners}
          className="touch-none cursor-grab"
        >
          <GripVerticalIcon />
        </Action>
      ) : (
        <span className="md:hidden" />
      )}
      <span
        className={
          ordered ? 'hidden text-xs text-muted-foreground md:block' : 'hidden'
        }
      >
        {sortable && count > 1 ? index + 1 : ''}
      </span>
      <div className="min-w-0">
        <button
          type="button"
          onClick={onView}
          className="max-w-full break-all text-left font-mono text-sm font-medium hover:underline"
        >
          {modelId}
        </button>
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
        {sortable && (
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
        )}
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
  sortable = true,
}: {
  group: string
  values: ModelConfig[]
  fallback: ModelConfig
  disabled: boolean
  onOrder: (values: ModelConfig[]) => void
  onOpen: (index: number, readonly: boolean) => void
  onDelete: (index: number) => void
  sortable?: boolean
}) {
  const { t } = useTranslation('settings')
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  )
  const ids = values.map((value, index) => `${group}-${value.id || index}`)
  function move(from: number, to: number) {
    if (from >= 0 && to >= 0 && to < values.length && from !== to)
      onOrder(arrayMove(values, from, to))
  }
  if (!values.length)
    return (
      <p className="py-4 text-sm text-muted-foreground">{t('models.empty')}</p>
    )
  return (
    <div className="min-w-0">
      <div
        className={`hidden gap-2 border-b pb-2 text-xs text-muted-foreground md:grid ${sortable && values.length > 1 ? 'grid-cols-[24px_32px_minmax(0,1.4fr)_100px_minmax(0,1.4fr)_80px_104px]' : 'grid-cols-[minmax(0,1.4fr)_100px_minmax(0,1.4fr)_80px_104px]'}`}
      >
        {sortable && values.length > 1 && (
          <>
            <span />
            <span>{t('models.priority')}</span>
          </>
        )}
        <span>{t('models.listModel')}</span>
        <span>{t('models.provider')}</span>
        <span>{t('models.apiBase')}</span>
        <span>{t('models.apiKey')}</span>
        <span className="text-right">{t('models.actions')}</span>
      </div>
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={({ active, over }) => {
          if (over && !disabled)
            move(ids.indexOf(String(active.id)), ids.indexOf(String(over.id)))
        }}
      >
        <SortableContext items={ids} strategy={verticalListSortingStrategy}>
          {values.map((value, index) => (
            <ModelRow
              key={ids[index]}
              id={ids[index]}
              value={value}
              fallback={fallback}
              index={index}
              count={values.length}
              disabled={disabled}
              sortable={sortable}
              onView={() => onOpen(index, true)}
              onEdit={() => onOpen(index, false)}
              onDelete={() => onDelete(index)}
              onMove={(to) => move(index, to)}
            />
          ))}
        </SortableContext>
      </DndContext>
      {!values.length && (
        <p className="py-5 text-sm text-muted-foreground">
          {t('models.empty')}
        </p>
      )}
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
  const {
    connection,
    connectionRole,
    serverMode,
    identityScopeKey,
    isConnectionRoleLoading,
  } = useAppConnection()
  const allowed =
    connectionRole === 'root' &&
    (serverMode === 'api_key' || serverMode === 'trusted')
  const api = React.useMemo(
    () => createConfigFileApi(connection, serverMode === 'trusted'),
    [connection, serverMode],
  )
  const queryClient = useQueryClient()
  const queryKey = ['server-configuration', identityScopeKey]
  const query = useQuery({
    queryKey,
    queryFn: api.get,
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
  const mutation = useMutation({
    mutationFn: (content: string) =>
      api.save(content, (baseline ?? query.data)!.revision),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey })
      setDraft(null)
      setEditor(null)
      setBaseline(null)
      setSaved(true)
      toast.success(t('models.saved'))
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
    }) => api.preview(content, changes),
    onSuccess: (result, variables) => {
      setDraft(result)
      setEditor(null)
      setConfirm(null)
      if (variables.returnToForm) setEditMode('form')
    },
  })
  const pending = mutation.isPending || preview.isPending
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
  ) {
    const config = current(kind)
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
      if (!mode && !config.model)
        change(kind, {
          ...config,
          model: String(next.model || ''),
          credentials: values,
        })
      else reorder(kind, mode, values)
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
  if (query.isError)
    return (
      <div role="alert" className="grid gap-3">
        <p>{t('models.loadFailed')}</p>
        {query.error instanceof Error && (
          <p className="break-all text-sm text-muted-foreground">
            {query.error.message}
          </p>
        )}
        <Button variant="outline" onClick={() => void query.refetch()}>
          {t('models.retry')}
        </Button>
      </div>
    )
  const hasOverrides = Boolean(
    query.data?.overrides?.cluster.length ||
    query.data?.overrides?.account.length,
  )
  const provider = String(editor?.value.provider || '')
  const fields = editor?.settings
    ? editor.kind === 'embedding'
      ? policies
      : ([
          { key: 'model', required: true },
          { key: 'timeout', type: 'number', min: 0.001 },
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
        <div
          className="flex flex-wrap items-center gap-2"
          role="group"
          aria-label={t('models.editMode')}
        >
          <Button
            variant={editMode === 'form' ? 'default' : 'outline'}
            disabled={pending || invalidJson}
            aria-pressed={editMode === 'form'}
            onClick={() => {
              if (editMode === 'form' || !document || pending) return
              setBaseline((previous) => previous ?? query.data ?? null)
              preview.mutate({ content: document.content, returnToForm: true })
            }}
          >
            {t('models.formMode')}
          </Button>
          <Button
            variant={editMode === 'file' ? 'default' : 'outline'}
            disabled={pending}
            aria-pressed={editMode === 'file'}
            onClick={() => setEditMode('file')}
          >
            {t('models.fileMode')}
          </Button>
        </div>
        <p className="text-sm text-muted-foreground">{t('models.fileScope')}</p>
        {preview.isError && !editor && !confirm && (
          <p role="alert" className="break-all text-destructive">
            {t('models.validateFailed')} {preview.error.message}
          </p>
        )}
        {editMode === 'file' ? (
          <div className="grid gap-3">
            <label
              htmlFor="startup-config-content"
              className="text-sm font-medium"
            >
              {t('models.fileContent')}
            </label>
            <Textarea
              id="startup-config-content"
              spellCheck={false}
              readOnly={pending || query.data?.writable === false}
              className="min-h-[28rem] w-full resize-y font-mono text-xs leading-6"
              value={document?.content ?? ''}
              onChange={(event) => {
                if (pending || !document) return
                setBaseline((previous) => previous ?? query.data ?? null)
                setSaved(false)
                mutation.reset()
                preview.reset()
                setDraft({ ...document, content: event.target.value })
              }}
            />
            {invalidJson && (
              <p role="alert" className="text-destructive">
                {t('models.invalidJsonObject')}
              </p>
            )}
          </div>
        ) : (
          visibleModelKinds.map((kind) => {
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
                            kind === 'embedding' ? groups[0]?.mode : undefined,
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
          })
        )}
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
                onClick={() => document && mutation.mutate(document.content)}
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
                {!editor.settings && (
                  <details>
                    <summary className="cursor-pointer text-sm text-muted-foreground">
                      {t('models.advanced')}
                    </summary>
                    <div className="mt-4 grid gap-4">
                      <ModelFields
                        key={provider}
                        fields={advancedFields(editor.kind)}
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
