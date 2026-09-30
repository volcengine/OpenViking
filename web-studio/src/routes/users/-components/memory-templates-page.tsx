import * as React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  ArrowLeftIcon,
  BookOpenTextIcon,
  BotIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  RotateCcwIcon,
  UserRoundIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Button } from '#/components/ui/button'
import { Textarea } from '#/components/ui/textarea'
import { useAppConnection } from '#/hooks/use-app-connection'
import type { AdminConnection } from '#/lib/admin'
import {
  editableMemoryTypes,
  fetchMemoryTemplates,
  resetMemoryTemplate,
  templateFieldValue,
  updateMemoryTemplate,
  withTemplateField,
} from '#/lib/memory-templates'
import type {
  EditableMemoryType,
  MemoryTemplate,
  MemoryTemplateSchema,
} from '#/lib/memory-templates'
import { resolveStudioManagementCapabilities } from '#/lib/studio-permissions'

const editableFields: Record<EditableMemoryType, string[]> = {
  profile: ['description', 'fields.content.description'],
  preferences: [
    'description',
    'fields.topic.description',
    'fields.content.description',
  ],
  entities: [
    'description',
    'fields.category.description',
    'fields.name.description',
    'fields.content.description',
  ],
  events: [
    'description',
    'fields.event_name.description',
    'fields.summary.description',
    'content_template',
  ],
  soul: [
    'description',
    'fields.core_truths.description',
    'fields.boundaries.description',
    'fields.vibe.description',
    'fields.continuity.description',
    'content_template',
  ],
  identity: [
    'description',
    'fields.creature.description',
    'fields.name.description',
    'fields.vibe.description',
    'fields.avatar.description',
    'fields.emoji.description',
    'fields.introduction.description',
    'content_template',
  ],
}

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message
  return String(error)
}

function fieldValidationError(field: string, value: string) {
  if (!value.trim()) return 'required'
  if (field !== 'content_template' && [...value].length > 50000) {
    return 'tooLong'
  }
  return null
}

export function MemoryTemplatesPage() {
  const { t } = useTranslation('settings')
  const { connection, connectionRole, isConnectionRoleLoading, serverMode } =
    useAppConnection()
  const { canManageUsers } = resolveStudioManagementCapabilities({
    hasControlCredential: Boolean(connection.adminApiKey.trim()),
    isRoleLoading: isConnectionRoleLoading,
    role: connectionRole,
    serverMode,
  })
  const adminConnection = React.useMemo<AdminConnection>(
    () => ({
      accountId: connection.accountId,
      apiKey: connection.adminApiKey,
      baseUrl: connection.baseUrl,
      userId: connection.userId,
    }),
    [
      connection.accountId,
      connection.adminApiKey,
      connection.baseUrl,
      connection.userId,
    ],
  )
  const queryClient = useQueryClient()
  const queryKey = [
    'account-memory-templates',
    adminConnection.baseUrl,
    adminConnection.apiKey,
    adminConnection.accountId,
  ]
  const templatesQuery = useQuery({
    queryKey,
    queryFn: () => fetchMemoryTemplates(adminConnection),
    enabled: canManageUsers && Boolean(adminConnection.accountId),
  })
  const [selected, setSelected] = React.useState<EditableMemoryType | null>(
    null,
  )
  const [editing, setEditing] = React.useState(false)
  const [draft, setDraft] = React.useState<MemoryTemplateSchema | null>(null)
  const [expanded, setExpanded] = React.useState<string>('description')
  const [resetPrompt, setResetPrompt] = React.useState(false)

  const template = templatesQuery.data?.templates.find(
    (item) => item.memory_type === selected,
  )
  const saveMutation = useMutation({
    mutationFn: (schema: MemoryTemplateSchema) =>
      updateMemoryTemplate(adminConnection, selected!, schema),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey })
      setEditing(false)
      setDraft(null)
      toast.success(t('memoryTemplates.saved'))
    },
    onError: (error) =>
      toast.error(t('memoryTemplates.saveFailed'), {
        description: errorMessage(error),
      }),
  })
  const resetMutation = useMutation({
    mutationFn: () => resetMemoryTemplate(adminConnection, selected!),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey })
      setEditing(false)
      setDraft(null)
      setResetPrompt(false)
      toast.success(t('memoryTemplates.resetDone'))
    },
    onError: (error) =>
      toast.error(t('memoryTemplates.resetFailed'), {
        description: errorMessage(error),
      }),
  })

  if (isConnectionRoleLoading) {
    return (
      <p className="py-16 text-center text-sm text-muted-foreground">
        {t('memoryTemplates.loading')}
      </p>
    )
  }
  if (!canManageUsers) {
    return (
      <div className="mx-auto max-w-xl rounded-2xl border bg-card p-8 text-center">
        <p className="mb-5 text-sm text-muted-foreground">
          {t('memoryTemplates.accessDenied')}
        </p>
        <Button nativeButton={false} render={<Link to="/settings" />}>
          {t('memoryTemplates.openConnection')}
        </Button>
      </div>
    )
  }
  if (templatesQuery.isPending) {
    return (
      <p className="py-16 text-center text-sm text-muted-foreground">
        {t('memoryTemplates.loading')}
      </p>
    )
  }
  if (templatesQuery.isError) {
    return (
      <div className="mx-auto max-w-xl rounded-2xl border bg-card p-8 text-center">
        <p className="mb-4 text-sm text-destructive">
          {errorMessage(templatesQuery.error)}
        </p>
        <Button variant="outline" onClick={() => void templatesQuery.refetch()}>
          {t('memoryTemplates.retry')}
        </Button>
      </div>
    )
  }

  const choose = (memoryType: EditableMemoryType) => {
    setSelected(memoryType)
    setEditing(false)
    setDraft(null)
    setExpanded('description')
  }
  const current = draft ?? template?.effective
  const changed = Boolean(
    draft &&
    template &&
    JSON.stringify(draft) !== JSON.stringify(template.effective),
  )
  const hasInvalidField = Boolean(
    selected &&
    current &&
    editableFields[selected].some((field) =>
      fieldValidationError(field, templateFieldValue(current, field)),
    ),
  )

  return (
    <div className="min-w-0">
      {!selected || !template ? (
        <div className="w-full min-w-0 space-y-7 pb-12">
          <header className="space-y-2">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-xl border border-primary/20 bg-primary/5 text-primary">
                <BookOpenTextIcon className="size-5" />
              </div>
              <h2 className="text-2xl font-semibold tracking-tight">
                {t('memoryTemplates.title')}
              </h2>
            </div>
            <p className="text-sm text-muted-foreground">
              {t('memoryTemplates.subtitle')}
            </p>
            <p className="text-xs text-muted-foreground">
              {t('memoryTemplates.account', { account: connection.accountId })}
            </p>
          </header>
          <TemplateGroup
            title={t('memoryTemplates.userGroup')}
            hint={t('memoryTemplates.userGroupHint')}
            icon={<UserRoundIcon className="size-12 stroke-[1.2]" />}
            types={editableMemoryTypes.slice(0, 4)}
            templates={templatesQuery.data.templates}
            onSelect={choose}
          />
          <TemplateGroup
            title={t('memoryTemplates.agentGroup')}
            hint={t('memoryTemplates.agentGroupHint')}
            icon={<BotIcon className="size-12 stroke-[1.2]" />}
            types={editableMemoryTypes.slice(4)}
            templates={templatesQuery.data.templates}
            onSelect={choose}
          />
        </div>
      ) : (
        <div className="grid min-h-[680px] gap-6 pb-20 xl:grid-cols-[minmax(0,1fr)_minmax(320px,22%)]">
          <main className="min-w-0 space-y-5">
            <header className="flex flex-wrap items-start justify-between gap-4 border-b pb-5">
              <div className="space-y-2">
                <button
                  type="button"
                  onClick={() => {
                    setSelected(null)
                    setEditing(false)
                    setDraft(null)
                  }}
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  <ArrowLeftIcon className="size-3.5" />{' '}
                  {t('memoryTemplates.back')}
                </button>
                <h2 className="text-2xl font-semibold">
                  {t(`memoryTemplates.${selected}`)}
                </h2>
                <p className="text-sm text-muted-foreground">
                  {t(`memoryTemplates.${selected}Hint`)}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground">
                  {t(
                    template.status === 'custom'
                      ? 'memoryTemplates.custom'
                      : 'memoryTemplates.default',
                  )}
                </span>
                {!editing ? (
                  <>
                    <Button
                      variant="outline"
                      disabled={template.status !== 'custom'}
                      onClick={() => setResetPrompt(true)}
                    >
                      <RotateCcwIcon className="size-4" />{' '}
                      {t('memoryTemplates.reset')}
                    </Button>
                    <Button
                      onClick={() => {
                        setDraft(structuredClone(template.effective))
                        setEditing(true)
                      }}
                    >
                      {t('memoryTemplates.edit')}
                    </Button>
                  </>
                ) : null}
              </div>
            </header>
            <div className="space-y-3">
              {editableFields[selected].map((field) => (
                <TemplateField
                  key={field}
                  field={field}
                  value={templateFieldValue(current!, field)}
                  defaultValue={templateFieldValue(template.defaults, field)}
                  expanded={expanded === field}
                  editing={editing}
                  onToggle={() => setExpanded(expanded === field ? '' : field)}
                  onChange={(value) =>
                    setDraft(withTemplateField(current!, field, value))
                  }
                  onReset={() =>
                    setDraft(
                      withTemplateField(
                        current!,
                        field,
                        templateFieldValue(template.defaults, field),
                      ),
                    )
                  }
                />
              ))}
            </div>
          </main>
          <aside className="min-w-0 rounded-2xl border bg-muted/30 p-4 xl:sticky xl:top-5 xl:self-start">
            <h3 className="mb-3 text-sm font-semibold">
              {t('memoryTemplates.example')}
            </h3>
            <div className="overflow-hidden rounded-xl border bg-card">
              <div className="break-all border-b bg-muted/20 px-4 py-3 font-mono text-xs text-muted-foreground">
                {`${template.defaults.directory}/${template.defaults.filename_template}`}
              </div>
              <div className="min-h-52 p-4">
                <pre className="whitespace-pre-wrap break-words rounded-lg border border-dashed bg-background p-4 font-mono text-xs leading-7 text-muted-foreground">
                  {t(`memoryTemplates.examples.${selected}`)}
                </pre>
              </div>
            </div>
            <p className="mt-3 text-xs leading-5 text-muted-foreground">
              {t('memoryTemplates.exampleHint')}
            </p>
          </aside>
        </div>
      )}
      {selected && editing && template ? (
        <div className="sticky bottom-0 z-10 -mx-4 flex flex-wrap items-center justify-end gap-3 border-t bg-background/95 py-3 pl-4 pr-20 backdrop-blur md:-mx-8 md:pl-8 md:pr-20">
          {changed ? (
            <span className="mr-auto text-xs text-muted-foreground">
              {t('memoryTemplates.changed')}
            </span>
          ) : null}
          <Button
            variant="outline"
            onClick={() => {
              setEditing(false)
              setDraft(null)
            }}
          >
            {t('memoryTemplates.cancel')}
          </Button>
          <Button
            disabled={!changed || hasInvalidField || saveMutation.isPending}
            onClick={() => {
              if (current) saveMutation.mutate(current)
            }}
          >
            {saveMutation.isPending
              ? t('memoryTemplates.saving')
              : t('memoryTemplates.save')}
          </Button>
        </div>
      ) : null}
      {resetPrompt && selected && template ? (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setResetPrompt(false)
          }}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="reset-template-title"
            className="w-full max-w-md rounded-2xl border bg-background p-6 shadow-xl"
          >
            <h3
              id="reset-template-title"
              className="mb-2 text-lg font-semibold"
            >
              {t('memoryTemplates.reset')}
            </h3>
            <p className="mb-6 text-sm leading-6 text-muted-foreground">
              {t('memoryTemplates.confirmReset')}
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setResetPrompt(false)}>
                {t('memoryTemplates.cancel')}
              </Button>
              <Button
                disabled={resetMutation.isPending}
                onClick={() => resetMutation.mutate()}
              >
                {t('memoryTemplates.confirm')}
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )
}

function TemplateGroup({
  title,
  hint,
  icon,
  types,
  templates,
  onSelect,
}: {
  title: string
  hint: string
  icon: React.ReactNode
  types: readonly EditableMemoryType[]
  templates: MemoryTemplate[]
  onSelect: (memoryType: EditableMemoryType) => void
}) {
  const { t } = useTranslation('settings')
  return (
    <section className="grid overflow-hidden rounded-2xl border bg-card md:grid-cols-[minmax(200px,25%)_minmax(0,1fr)]">
      <div className="relative flex min-h-52 items-center justify-center overflow-hidden border-b bg-muted/15 text-muted-foreground/60 md:border-r md:border-b-0">
        <div className="absolute size-44 rounded-full border border-border/60" />
        <div className="absolute size-32 rounded-full border border-border/70" />
        <div className="relative flex size-20 items-center justify-center rounded-full border bg-background shadow-sm">
          {icon}
        </div>
      </div>
      <div className="min-w-0 px-5 py-5 md:px-7">
        <h3 className="mb-4 flex flex-wrap items-baseline gap-x-2 text-base font-semibold">
          {title}
          <span className="text-sm font-normal text-muted-foreground">
            · {hint}
          </span>
        </h3>
        <div className="divide-y">
          {types.map((type) => {
            const template = templates.find((item) => item.memory_type === type)
            return (
              <button
                key={type}
                type="button"
                onClick={() => onSelect(type)}
                className="group flex w-full items-center justify-between gap-4 py-4 text-left first:pt-1 last:pb-0 hover:text-primary"
              >
                <span className="min-w-0">
                  <span className="flex items-center gap-2 font-medium">
                    {t('memoryTemplates.fileName', {
                      type: t(`memoryTemplates.${type}`),
                    })}
                    {template?.status === 'custom' ? (
                      <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[10px] text-primary">
                        {t('memoryTemplates.custom')}
                      </span>
                    ) : null}
                  </span>
                  <span className="mt-1 block text-sm leading-6 text-muted-foreground">
                    {t(`memoryTemplates.${type}Hint`)}
                  </span>
                </span>
                <ChevronRightIcon className="size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-1 group-hover:text-primary" />
              </button>
            )
          })}
        </div>
      </div>
    </section>
  )
}

function TemplateField({
  field,
  value,
  defaultValue,
  expanded,
  editing,
  onToggle,
  onChange,
  onReset,
}: {
  field: string
  value: string
  defaultValue: string
  expanded: boolean
  editing: boolean
  onToggle: () => void
  onChange: (value: string) => void
  onReset: () => void
}) {
  const { t } = useTranslation('settings')
  const label =
    field === 'description'
      ? t('memoryTemplates.typeDescription')
      : field === 'content_template'
        ? t('memoryTemplates.contentTemplate')
        : t(`memoryTemplates.fields.${field.split('.')[1] as 'content'}`)
  const validationError = editing ? fieldValidationError(field, value) : null
  return (
    <section className="overflow-hidden rounded-xl border bg-card">
      <div className="flex items-start justify-between gap-3 p-4">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className="min-w-0 flex-1 text-left"
        >
          <span className="flex flex-wrap items-baseline gap-2">
            <span className="font-semibold">{label}</span>
            <code className="text-xs text-muted-foreground">{field}</code>
          </span>
          <span className="mt-1 block text-xs leading-5 text-muted-foreground">
            {field === 'description'
              ? t('memoryTemplates.typeDescriptionHint')
              : field === 'content_template'
                ? t('memoryTemplates.contentTemplateHint')
                : t('memoryTemplates.fieldDescription')}
          </span>
        </button>
        {editing && value !== defaultValue ? (
          <button
            type="button"
            onClick={onReset}
            className="shrink-0 text-xs text-primary hover:underline"
          >
            {t('memoryTemplates.resetField')}
          </button>
        ) : null}
        <button
          type="button"
          onClick={onToggle}
          aria-label={label}
          className="pt-1 text-muted-foreground"
        >
          <ChevronDownIcon
            className={`size-4 transition-transform ${expanded ? 'rotate-180' : ''}`}
          />
        </button>
      </div>
      {expanded ? (
        <div className="px-4 pb-4">
          {editing ? (
            <>
              <Textarea
                value={value}
                onChange={(event) => onChange(event.target.value)}
                aria-label={label}
                className="min-h-56 resize-y font-mono text-xs leading-6"
              />
              <div className="mt-2 flex justify-end text-xs text-muted-foreground">
                {[...value].length} /{' '}
                {field === 'content_template' ? '—' : '50000'}
              </div>
              {validationError ? (
                <p className="text-xs text-destructive">
                  {t(`memoryTemplates.${validationError}`)}
                </p>
              ) : null}
              {field !== 'content_template' ? (
                <div className="mt-3 rounded-lg bg-muted/40 p-3 text-xs">
                  <div className="font-medium">
                    {t('memoryTemplates.variable')}
                  </div>
                  <p className="my-1 text-muted-foreground">
                    {t('memoryTemplates.variableHint')}
                  </p>
                  <button
                    type="button"
                    onClick={() => onChange(`${value}{{ language }}`)}
                    className="rounded-md border bg-background px-2 py-1 font-mono hover:border-primary"
                  >
                    {t('memoryTemplates.languageVariable')}
                  </button>
                  <details className="mt-3">
                    <summary className="cursor-pointer font-medium">
                      {t('memoryTemplates.syntax')}
                    </summary>
                    <p className="mt-2 leading-5 text-muted-foreground">
                      {t('memoryTemplates.syntaxHint')}
                    </p>
                    <code className="mt-1 block break-all text-primary">
                      {t('memoryTemplates.syntaxExample')}
                    </code>
                  </details>
                </div>
              ) : null}
            </>
          ) : (
            <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted/50 p-4 font-mono text-xs leading-6 text-foreground/80">
              {value}
            </pre>
          )}
        </div>
      ) : null}
    </section>
  )
}
