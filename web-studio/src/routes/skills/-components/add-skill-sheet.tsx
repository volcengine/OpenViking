import * as React from 'react'
import { LoaderCircleIcon, UploadIcon, XIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '#/components/ui/sheet'
import { Textarea } from '#/components/ui/textarea'
import { ovClient } from '#/lib/ov-client'

import {
  addNewSkill,
  fetchSkillTask,
  validateNewSkill,
} from '../-lib/add-skill'
import type { SkillScope } from './skill-scope-tabs'

type Phase =
  | 'idle'
  | 'validating'
  | 'adding'
  | 'processing'
  | 'completed'
  | 'failed'

const SCOPES: SkillScope[] = ['user', 'agent']

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

export function AddSkillSheet({
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
  const [fileName, setFileName] = React.useState('')
  const [scope, setScope] = React.useState<SkillScope>('user')
  const [phase, setPhase] = React.useState<Phase>('idle')
  const [taskId, setTaskId] = React.useState('')
  const [errors, setErrors] = React.useState<string[]>([])
  const [warnings, setWarnings] = React.useState<string[]>([])
  const previousOpen = React.useRef(false)
  const fileInputRef = React.useRef<HTMLInputElement>(null)
  const operationGeneration = React.useRef(0)

  React.useLayoutEffect(() => {
    if (!open) operationGeneration.current += 1
    return () => {
      // A closed drawer or a workspace change must invalidate pending writes.
      operationGeneration.current += 1
    }
  }, [open])

  const reset = React.useCallback(() => {
    setContent('')
    setFileName('')
    setScope('user')
    setPhase('idle')
    setTaskId('')
    setErrors([])
    setWarnings([])
    if (fileInputRef.current) fileInputRef.current.value = ''
  }, [])

  React.useEffect(() => {
    if (open && !previousOpen.current) reset()
    previousOpen.current = open
  }, [open, reset])

  function removeFile() {
    setContent('')
    setFileName('')
    setPhase('idle')
    setTaskId('')
    setErrors([])
    setWarnings([])
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  React.useEffect(() => {
    if (phase !== 'processing' || !taskId) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const task = await fetchSkillTask(taskId)
        if (cancelled) return
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
        if (cancelled) return
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

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (
      !content.trim() ||
      phase === 'validating' ||
      phase === 'adding' ||
      phase === 'processing'
    )
      return
    setErrors([])
    setWarnings([])
    setPhase('validating')
    const generation = operationGeneration.current
    const sourceConnectionIdentity = currentConnectionIdentity()
    try {
      const validation = await validateNewSkill(content)
      if (
        generation !== operationGeneration.current ||
        sourceConnectionIdentity !== currentConnectionIdentity()
      )
        return
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
      setPhase('adding')
      const result = await addNewSkill(content, scope)
      if (generation !== operationGeneration.current) return
      onAdded(scope)
      if (result.task_id) {
        setTaskId(result.task_id)
        setPhase('processing')
      } else {
        setPhase('completed')
      }
    } catch (error) {
      if (generation !== operationGeneration.current) return
      setErrors([error instanceof Error ? error.message : String(error)])
      setPhase('failed')
    }
  }

  async function loadFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    if (!file) return
    try {
      setContent(await file.text())
      setFileName(file.name)
      setErrors([])
      setWarnings([])
      setPhase('idle')
    } catch (error) {
      setErrors([error instanceof Error ? error.message : String(error)])
    }
    event.target.value = ''
  }

  const busy =
    phase === 'validating' || phase === 'adding' || phase === 'processing'

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="gap-0 data-[side=right]:sm:max-w-xl">
        <SheetHeader className="border-b px-6 py-5">
          <SheetTitle className="text-lg">{t('add.title')}</SheetTitle>
          <SheetDescription>{t('add.description')}</SheetDescription>
        </SheetHeader>
        <form
          onSubmit={(event) => void submit(event)}
          className="flex min-h-0 flex-1 flex-col"
        >
          <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-6 py-5">
            <fieldset className="grid gap-2" disabled={busy}>
              <legend className="mb-2 text-sm font-medium">
                {t('add.scope')}
              </legend>
              {SCOPES.map((value) => (
                <label
                  key={value}
                  className="flex cursor-pointer items-center gap-3 rounded-lg border border-border px-4 py-3 has-checked:border-foreground has-checked:bg-muted/30"
                >
                  <input
                    type="radio"
                    name="skill-scope"
                    value={value}
                    checked={scope === value}
                    onChange={() => setScope(value)}
                    className="accent-foreground"
                  />
                  <span className="grid gap-0.5">
                    <span className="font-medium">{t(`scopes.${value}`)}</span>
                    <span className="text-xs text-muted-foreground">
                      {t(`add.scopeDescription.${value}`)}
                    </span>
                  </span>
                </label>
              ))}
            </fieldset>

            <div className="grid gap-2">
              <label htmlFor="skill-content" className="text-sm font-medium">
                {t('add.content')}
              </label>
              <p className="text-xs text-muted-foreground">
                {t('add.contentHint')}
              </p>
              <Textarea
                id="skill-content"
                value={content}
                onChange={(event) => {
                  setContent(event.target.value)
                  setFileName('')
                  setErrors([])
                  setWarnings([])
                  setPhase('idle')
                }}
                placeholder={t('add.placeholder')}
                className="min-h-64 resize-y font-mono text-xs leading-5"
                spellCheck={false}
                disabled={busy}
              />
              <div className="mt-2 flex min-w-0 flex-wrap items-center gap-2">
                <label className="inline-flex w-fit cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-muted/50 focus-within:ring-2 focus-within:ring-ring">
                  <UploadIcon className="size-4" />
                  {t('add.upload')}
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".md,text/markdown"
                    className="sr-only"
                    onChange={(event) => void loadFile(event)}
                    disabled={busy}
                  />
                </label>
                {fileName ? (
                  <div className="flex min-w-0 items-center gap-1 rounded-md bg-muted px-2 py-1 text-sm">
                    <span className="max-w-56 truncate" title={fileName}>
                      {fileName}
                    </span>
                    <button
                      type="button"
                      onClick={removeFile}
                      disabled={busy}
                      aria-label={t('add.removeFile', { fileName })}
                      className="rounded p-1 hover:bg-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <XIcon className="size-4" />
                    </button>
                  </div>
                ) : null}
              </div>
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
                  {t(
                    phase === 'completed' ? 'add.completed' : 'add.processing',
                  )}
                </p>
                {taskId ? (
                  <p className="mt-1 break-all text-xs text-muted-foreground">
                    {t('add.taskId')}: {taskId}
                  </p>
                ) : null}
              </div>
            ) : null}
          </div>
          <SheetFooter className="border-t px-6 py-4 sm:flex-row sm:justify-end">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              {t('add.close')}
            </Button>
            {phase === 'completed' ? (
              <Button type="button" onClick={reset}>
                {t('add.another')}
              </Button>
            ) : (
              <Button type="submit" disabled={!content.trim() || busy}>
                {busy ? (
                  <LoaderCircleIcon className="size-4 animate-spin" />
                ) : null}
                {t(
                  phase === 'validating'
                    ? 'add.validating'
                    : phase === 'adding'
                      ? 'add.adding'
                      : 'add.submit',
                )}
              </Button>
            )}
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  )
}
