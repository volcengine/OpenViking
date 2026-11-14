import {
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  Github,
  LoaderCircleIcon,
  XIcon,
} from 'lucide-react'
import * as React from 'react'
import { useDropzone } from 'react-dropzone'
import { useTranslation } from 'react-i18next'

import { ovClient } from '#/shared/api/ov-client/index'
import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogTitle,
} from '#/shared/ui/dialog'
import { Input } from '#/shared/ui/input'
import { RadioGroup, RadioGroupItem } from '#/shared/ui/radio-group'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '#/shared/ui/tabs'
import { cn } from '#/shared/lib/utils'
import { formatFileSize } from '#/features/workbench/resources/lib/upload'
import type { SkillSource } from '#/features/workbench/skills/lib/add-skill'

import type { SkillScope } from '#/features/workbench/skills/components/skill-scope-tabs'
import {
  addNewSkill,
  fetchSkillTask,
  listSkillSources,
  validateNewSkill,
} from '#/features/workbench/skills/lib/add-skill'

type Phase =
  | 'idle'
  | 'reading'
  | 'validating'
  | 'adding'
  | 'processing'
  | 'completed'
  | 'failed'

const SCOPES: SkillScope[] = ['user', 'agent']
const GITHUB_EXAMPLE = 'https://github.com/volcengine/OpenViking'
const GITHUB_LINK =
  /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+(?:\/tree\/[^\s/?#]+(?:\/[^\s?#]+)?)?\/?$/i

function currentConnectionIdentity(): string {
  const options = ovClient.getOptions()
  const connection = ovClient.getConnection()
  return JSON.stringify([
    options.baseUrl,
    options.apiKeyAuth,
    connection.accountId,
    connection.userId,
    connection.apiKey,
    connection.adminApiKey,
    connection.identityHeaders,
  ])
}

export function ImportSkillDialog({
  open,
  onOpenChange,
  onAdded,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onAdded: (scope: SkillScope) => void
}) {
  const { t } = useTranslation('skillsPage')
  const [content, setContent] = React.useState('')
  const [file, setFile] = React.useState<File | null>(null)
  const [mode, setMode] = React.useState<'upload' | 'github'>('upload')
  const [url, setUrl] = React.useState('')
  const [sources, setSources] = React.useState<SkillSource[]>([])
  const [sourcePath, setSourcePath] = React.useState('')
  const [scope, setScope] = React.useState<SkillScope>('user')
  const [phase, setPhase] = React.useState<Phase>('idle')
  const [taskId, setTaskId] = React.useState('')
  const [errors, setErrors] = React.useState<string[]>([])
  const [warnings, setWarnings] = React.useState<string[]>([])
  const previousOpen = React.useRef(false)
  const operationGeneration = React.useRef(0)

  React.useLayoutEffect(() => {
    if (!open) operationGeneration.current += 1
    return () => {
      // A closed dialog or a workspace change must invalidate pending writes.
      operationGeneration.current += 1
    }
  }, [open])

  const reset = React.useCallback(() => {
    setContent('')
    setFile(null)
    setMode('upload')
    setUrl('')
    setSources([])
    setSourcePath('')
    setScope('user')
    setPhase('idle')
    setTaskId('')
    setErrors([])
    setWarnings([])
  }, [])

  React.useEffect(() => {
    if (open && !previousOpen.current) reset()
    previousOpen.current = open
  }, [open, reset])

  function removeFile() {
    setContent('')
    setFile(null)
    setPhase('idle')
    setTaskId('')
    setErrors([])
    setWarnings([])
  }

  React.useEffect(() => {
    if (phase !== 'processing' || !taskId) return
    let cancelled = false
    const connection = currentConnectionIdentity()
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const task = await fetchSkillTask(taskId)
        if (cancelled || connection !== currentConnectionIdentity()) return
        if (task.status === 'completed') {
          setPhase('completed')
          onAdded(scope)
        } else if (task.status === 'failed' || task.status === 'cancelled') {
          setErrors([
            typeof task.error === 'string' && task.error
              ? task.error
              : t('add.processingFailed'),
          ])
          setPhase('failed')
        } else {
          timer = setTimeout(() => void poll(), 2000)
        }
      } catch (error) {
        if (cancelled || connection !== currentConnectionIdentity()) return
        setErrors([error instanceof Error ? error.message : String(error)])
        setPhase('failed')
      }
    }
    void poll()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [phase, taskId, onAdded, scope, t])

  const busy = ['reading', 'validating', 'adding', 'processing'].includes(phase)
  const data = mode === 'upload' ? content : url.trim()
  const canSubmit =
    Boolean(data.trim()) &&
    !busy &&
    (mode !== 'github' || sources.length === 0 || Boolean(sourcePath))

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSubmit || phase === 'completed') return
    setErrors([])
    setWarnings([])
    setTaskId('')
    setPhase('validating')
    const generation = operationGeneration.current
    const connection = currentConnectionIdentity()
    const isCurrent = () =>
      generation === operationGeneration.current &&
      connection === currentConnectionIdentity()
    try {
      let selectedPath = mode === 'github' ? sourcePath : ''
      if (mode === 'github') {
        if (!GITHUB_LINK.test(data)) {
          setErrors([t('add.invalidGithub')])
          setPhase('idle')
          return
        }
        if (sources.length === 0) {
          const matches = await listSkillSources(data)
          if (!isCurrent()) return
          if (matches.length === 0) {
            setErrors([t('add.noSkills')])
            setPhase('idle')
            return
          }
          if (matches.length > 1) {
            setSources(matches)
            setPhase('idle')
            return
          }
          selectedPath = matches[0].path
        }
      } else {
        const validation = await validateNewSkill(data)
        if (!isCurrent()) return
        setWarnings(
          validation.warnings.map((issue) => issue.message || issue.rule || ''),
        )
        if (!validation.valid) {
          setErrors(
            validation.errors.map((issue) => issue.message || issue.rule || ''),
          )
          setPhase('idle')
          return
        }
      }
      setPhase('adding')
      const result = await addNewSkill(data, scope, selectedPath || undefined)
      if (!isCurrent()) return
      onAdded(scope)
      if (result.task_id) {
        setTaskId(result.task_id)
        setPhase('processing')
      } else {
        setPhase('completed')
      }
    } catch (error) {
      if (!isCurrent()) return
      setErrors([error instanceof Error ? error.message : String(error)])
      setPhase('failed')
    }
  }

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: { 'text/markdown': ['.md'] },
    disabled: busy,
    multiple: false,
    onDrop: (accepted, rejected) => {
      if (rejected.length > 0) {
        setErrors([t('add.invalidFile')])
        return
      }
      const selected = accepted.at(0)
      if (!selected) return
      const generation = operationGeneration.current
      const connection = currentConnectionIdentity()
      setContent('')
      setErrors([])
      setWarnings([])
      setPhase('reading')
      void selected.text().then(
        (value) => {
          if (
            generation !== operationGeneration.current ||
            connection !== currentConnectionIdentity()
          )
            return
          if (!value.trim()) {
            setErrors([t('add.invalidFile')])
          } else {
            setContent(value)
            setFile(selected)
          }
          setPhase('idle')
        },
        (error: unknown) => {
          if (
            generation !== operationGeneration.current ||
            connection !== currentConnectionIdentity()
          )
            return
          setErrors([error instanceof Error ? error.message : String(error)])
          setPhase('failed')
        },
      )
    },
  })

  const sourceLabelId = React.useId()
  const targetLabelId = React.useId()
  const userId = ovClient.getConnection().userId || 'default'
  const targetUris: Record<SkillScope, string> = {
    user: `viking://user/${userId}/skills`,
    agent: 'viking://agent/skills',
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-workbench-overlay
        showCloseButton={false}
        overlayClassName="bg-black/50 backdrop-blur-none"
        className="gap-5 rounded-2xl sm:max-w-[560px]"
      >
        <DialogTitle className="text-xl leading-7">
          {t('add.title')}
        </DialogTitle>
        <form
          onSubmit={(event) => void submit(event)}
          className="min-w-0 space-y-5"
        >
          <Tabs
            value={mode}
            onValueChange={(value) => {
              setMode(value === 'github' ? 'github' : 'upload')
              setErrors([])
              setWarnings([])
              setTaskId('')
              setPhase('idle')
            }}
            className="gap-4"
          >
            <TabsList className="w-full">
              <TabsTrigger
                value="upload"
                disabled={busy || phase === 'completed'}
              >
                <FolderOpen />
                {t('add.uploadTab')}
              </TabsTrigger>
              <TabsTrigger
                value="github"
                disabled={busy || phase === 'completed'}
              >
                <Github />
                {t('add.githubTab')}
              </TabsTrigger>
            </TabsList>
            <TabsContent value="upload">
              {file ? (
                <div className="flex min-h-14 min-w-0 items-center gap-2 rounded-xl border px-4 py-2">
                  <FileText className="size-4 shrink-0 text-emerald-600" />
                  <span className="truncate" title={file.name}>
                    {file.name}
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {formatFileSize(file.size)}
                  </span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    className="ml-auto"
                    aria-label={t('add.removeFile', { fileName: file.name })}
                    disabled={busy || phase === 'completed'}
                    onClick={removeFile}
                  >
                    <XIcon />
                  </Button>
                </div>
              ) : (
                <div
                  {...getRootProps({
                    role: 'button',
                    'aria-label': t('add.upload'),
                  })}
                  className={cn(
                    'flex min-h-40 cursor-pointer flex-col items-center justify-center gap-4 rounded-xl border border-dashed border-border bg-muted/50 p-4 text-center focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary',
                    isDragActive && 'border-primary bg-primary/5',
                  )}
                >
                  <input
                    {...getInputProps({ 'aria-label': t('add.upload') })}
                  />
                  <FolderPlus className="size-6" aria-hidden="true" />
                  <span>{t('add.dropzone')}</span>
                </div>
              )}
            </TabsContent>
            <TabsContent value="github" className="space-y-3">
              <Input
                type="url"
                autoComplete="off"
                aria-label={t('add.githubTab')}
                placeholder={GITHUB_EXAMPLE}
                className="h-11"
                value={url}
                disabled={busy || phase === 'completed'}
                onChange={(event) => {
                  setUrl(event.target.value)
                  setSources([])
                  setSourcePath('')
                  setTaskId('')
                  setErrors([])
                  setPhase('idle')
                }}
              />
              {sources.length > 0 && (
                <div className="space-y-2">
                  <p id={sourceLabelId} className="text-sm">
                    {t('add.selectSkill')}
                  </p>
                  <RadioGroup
                    aria-labelledby={sourceLabelId}
                    value={sourcePath}
                    onValueChange={(value) => setSourcePath(String(value))}
                    disabled={busy || phase === 'completed'}
                    className="max-h-40 gap-0 divide-y overflow-y-auto rounded-xl border"
                  >
                    {sources.map((source) => {
                      const ambiguous = sources.some(
                        (other) =>
                          other !== source && other.path === source.path,
                      )
                      return (
                        <label
                          key={`${source.path}:${source.name}`}
                          className="flex items-center gap-2 px-3 py-2 has-disabled:opacity-50"
                        >
                          <RadioGroupItem
                            value={source.path}
                            disabled={ambiguous}
                          />
                          <span className="min-w-0">
                            <span className="block truncate">
                              {source.name}
                            </span>
                            <span className="block truncate text-xs text-muted-foreground">
                              {ambiguous
                                ? t('add.ambiguousSkill')
                                : source.description}
                            </span>
                          </span>
                        </label>
                      )
                    })}
                  </RadioGroup>
                </div>
              )}
            </TabsContent>
          </Tabs>
          <div className="space-y-2">
            <p id={targetLabelId}>{t('add.scope')}</p>
            <RadioGroup
              aria-labelledby={targetLabelId}
              value={scope}
              onValueChange={(value) =>
                setScope(value === 'agent' ? 'agent' : 'user')
              }
              disabled={busy || phase === 'completed'}
              className="gap-0 rounded-xl border px-3"
            >
              {SCOPES.map((value) => (
                <label
                  key={value}
                  className="flex min-h-11 cursor-pointer items-center gap-2 border-b py-2 last:border-b-0"
                >
                  <RadioGroupItem value={value} />
                  <Folder className="size-4 shrink-0" aria-hidden="true" />
                  <span className="min-w-0 flex-1 break-all">
                    {targetUris[value]}
                  </span>
                  <span className="shrink-0 text-muted-foreground">
                    {t(`add.targets.${value}`)}
                  </span>
                </label>
              ))}
            </RadioGroup>
          </div>

          {warnings.length > 0 ? (
            <div
              role="status"
              className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm"
            >
              <p className="font-medium">{t('add.warnings')}</p>
              <ul className="mt-1 list-inside list-disc text-muted-foreground">
                {warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {errors.length > 0 ? (
            <div
              role="alert"
              className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
            >
              <p className="font-medium">
                {t(taskId ? 'add.processingFailed' : 'add.failed')}
              </p>
              <ul className="mt-1 list-inside list-disc">
                {errors.map((error, index) => (
                  <li key={index}>{error}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {phase === 'processing' || phase === 'completed' ? (
            <div
              role="status"
              className="rounded-lg border bg-muted/30 p-3 text-sm"
            >
              <p className="font-medium">
                {t(phase === 'completed' ? 'add.completed' : 'add.processing')}
              </p>
              {taskId ? (
                <p className="mt-1 break-all text-xs text-muted-foreground">
                  {t('add.taskId')}: {taskId}
                </p>
              ) : null}
            </div>
          ) : null}
          <DialogFooter className="flex-row justify-end gap-3">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => onOpenChange(false)}
            >
              {t(
                phase === 'processing' || phase === 'completed'
                  ? 'add.close'
                  : 'add.cancel',
              )}
            </Button>
            {phase === 'completed' ? (
              <Button type="button" size="sm" onClick={reset}>
                {t('add.another')}
              </Button>
            ) : (
              <Button type="submit" size="sm" disabled={!canSubmit}>
                {busy ? (
                  <LoaderCircleIcon className="size-4 animate-spin" />
                ) : null}
                {t(
                  phase === 'processing'
                    ? 'add.processingButton'
                    : phase === 'validating' || phase === 'reading'
                      ? 'add.validating'
                      : phase === 'adding'
                        ? 'add.adding'
                        : 'add.submit',
                )}
              </Button>
            )}
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
