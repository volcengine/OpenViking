import { ArgsEditor } from './args-editor'
import { readCompileHandoff, clearCompileHandoff } from '../-lib/handoff'
import { isRejectedCompileSubmission } from '../-lib/errors'
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate, useBlocker } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { isOvClientError } from '#/lib/ov-client'
import { FolderOpen, Plus, X } from 'lucide-react'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { Textarea } from '#/components/ui/textarea'
import { useAppConnection } from '#/hooks/use-app-connection'
import { DirectoryPickerDialog } from '#/routes/resources/-components/directory-picker-dialog'
import { fetchFileContent } from '#/routes/resources/-lib/api'
import {
  createCompile,
  fetchCompileSkills,
  fetchCompileTask,
  lookupSubmission,
} from '../-lib/api'
import type { CompileRequest, CompileTask } from '../-lib/api'
import { compileCommand, parseArgs } from '../-lib/commands'
import { CompileError, CompileShell } from './shared'
import { SkillPicker } from './skill-picker'

type Draft = { skill: string; sources: string; to: string; instruction: string }
const empty: Draft = { skill: '', sources: '', to: '', instruction: '' }
function readDraft(key: string): Draft {
  try {
    const data = JSON.parse(localStorage.getItem(key) || '{}')
    return Object.fromEntries(
      Object.keys(empty).map((field) => [
        field,
        typeof data?.[field] === 'string' ? data[field] : '',
      ]),
    ) as Draft
  } catch {
    return empty
  }
}
export function CompileForm({ fromTask }: { fromTask?: string }) {
  const { t } = useTranslation('compile'),
    navigate = useNavigate()
  const { identityScopeKey, connection } = useAppConnection()
  const userResourceRoot = `viking://user/${connection.userId || 'default'}/resources/`
  const draftKey = `compile-draft:${identityScopeKey}`,
    submissionKey = `compile-submission:${identityScopeKey}`
  const [handoff] = useState(() => readCompileHandoff(identityScopeKey))
  useEffect(() => {
    clearCompileHandoff()
  }, [])
  const [draft, setDraft] = useState(() =>
    handoff
      ? {
          skill: handoff.skill || '',
          sources: handoff.from?.join('\n') || '',
          to: handoff.to || '',
          instruction: handoff.instruction || '',
        }
      : readDraft(draftKey),
  )
  const [args, setArgs] = useState(
      handoff?.args ? JSON.stringify(handoff.args, null, 2) : '',
    ),
    [preview, setPreview] = useState(false)
  const [picker, setPicker] = useState<'sources' | 'to' | null>(null)
  const [error, setError] = useState<unknown>(null),
    [savingError, setSavingError] = useState(false)
  const [busy, setBusy] = useState(false),
    [uncertain, setUncertain] = useState(false)
  const submission = useRef<{ key: string; request: CompileRequest } | null>(
    null,
  )
  const mounted = useRef(true),
    completed = useRef(false),
    filled = useRef(false)
  const [recoverKey, setRecoverKey] = useState(() => {
    try {
      return sessionStorage.getItem(submissionKey)
    } catch {
      return null
    }
  })
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  useBlocker({
    shouldBlockFn: () =>
      !completed.current &&
      (busy || !!args.trim() || savingError) &&
      !window.confirm(t('leave')),
    enableBeforeUnload: () => !completed.current && !!args.trim(),
  })
  useEffect(() => {
    if (completed.current) return
    try {
      localStorage.setItem(draftKey, JSON.stringify(draft))
      setSavingError(false)
    } catch {
      setSavingError(true)
    }
  }, [draft, draftKey])
  const skills = useQuery({
    queryKey: ['compile-skills', identityScopeKey],
    queryFn: ({ signal }) => fetchCompileSkills(signal),
  })
  const original = useQuery({
    queryKey: ['compile-task', identityScopeKey, fromTask],
    queryFn: ({ signal }) => fetchCompileTask(fromTask!, signal),
    enabled: !!fromTask,
  })
  useEffect(() => {
    const originalRequest = original.data?.meta?.request
    if (!originalRequest || filled.current) return
    filled.current = true
    if (
      Object.values(readDraft(draftKey)).some(Boolean) &&
      !window.confirm(t('selectDraft'))
    )
      return
    setDraft({
      skill: originalRequest.skill,
      sources: originalRequest.from.join('\n'),
      to: originalRequest.to,
      instruction: originalRequest.instruction || '',
    })
    setArgs(
      originalRequest.args ? JSON.stringify(originalRequest.args, null, 2) : '',
    )
  }, [original.data, draftKey, t])
  const skillContent = useQuery({
    queryKey: ['compile-skill-content', identityScopeKey, draft.skill],
    queryFn: () =>
      fetchFileContent(`${draft.skill.replace(/\/$/, '')}/SKILL.md`),
    enabled: preview && !!draft.skill,
  })
  function update(key: keyof Draft, value: string) {
    setDraft((v) => ({ ...v, [key]: value }))
  }
  const [attempted, setAttempted] = useState(false)
  const sourceRows = draft.sources ? draft.sources.split('\n') : ['']
  const fieldErrors = {
    skill: !draft.skill ? t('skillRequired') : '',
    sources: !draft.sources.trim()
      ? t('sourcesRequired')
      : sourceRows.some((v) => v.trim() && !v.trim().startsWith('viking://'))
        ? t('invalidUri')
        : '',
    to: !draft.to.trim()
      ? t('targetRequired')
      : !draft.to.trim().startsWith('viking://')
        ? t('invalidUri')
        : '',
    args: (() => {
      try {
        parseArgs(args)
        return ''
      } catch {
        return t('argsError')
      }
    })(),
  }
  function validate() {
    setAttempted(true)
    const first = (
      Object.keys(fieldErrors) as (keyof typeof fieldErrors)[]
    ).find((key) => fieldErrors[key])
    if (!first) return true
    if (first === 'args')
      document.getElementById('compile-advanced')?.setAttribute('open', '')
    document
      .getElementById(`compile-${first === 'to' ? 'target' : first}`)
      ?.focus()
    return false
  }
  function fieldError(key: keyof typeof fieldErrors) {
    return attempted && fieldErrors[key] ? (
      <p id={`error-${key}`} role="alert" className="text-xs text-destructive">
        {fieldErrors[key]}
      </p>
    ) : null
  }
  function request(): CompileRequest {
    const sources = [
      ...new Set(
        draft.sources
          .split('\n')
          .map((v) => v.trim().replace(/\/$/, ''))
          .filter(Boolean),
      ),
    ]
    if (!draft.skill || !sources.length || !draft.to.trim())
      throw new Error(t('required'))
    if (
      [...sources, draft.skill, draft.to.trim()].some(
        (uri) => !uri.startsWith('viking://'),
      )
    )
      throw new Error(t('invalidUri'))
    let parsed: Record<string, unknown> | undefined
    try {
      parsed = parseArgs(args)
    } catch {
      throw new Error(t('argsError'))
    }
    return {
      from: sources,
      skill: draft.skill,
      to: draft.to.trim(),
      ...(draft.instruction.trim()
        ? { instruction: draft.instruction.trim() }
        : {}),
      ...(parsed ? { args: parsed } : {}),
    }
  }
  async function finish(task: CompileTask) {
    if (!mounted.current) return
    completed.current = true
    try {
      localStorage.removeItem(draftKey)
      sessionStorage.removeItem(submissionKey)
    } catch {
      /* storage is optional */
    }
    await navigate({
      to: '/compile/tasks/$taskId',
      params: { taskId: task.task_id },
    })
  }
  async function submit() {
    if (busy) return
    setError(null)
    const restoredKey = submission.current ? null : recoverKey
    setBusy(true)
    try {
      if (restoredKey) {
        try {
          const task = await lookupSubmission(restoredKey)
          await finish(task)
          return
        } catch (cause) {
          if (!isOvClientError(cause) || cause.code !== 'NOT_FOUND') throw cause
        }
        if (!mounted.current) return
        const submittedAt = Number(restoredKey.split(':')[0])
        if (
          !Number.isFinite(submittedAt) ||
          Date.now() - submittedAt > 86400000
        )
          throw new Error(t('expiredSubmission'))
      }
      if (!submission.current && !validate()) return
      const body = submission.current?.request || request()
      if (!submission.current)
        submission.current = {
          key: recoverKey || `${Date.now()}:${crypto.randomUUID()}`,
          request: body,
        }
      const key = submission.current.key
      try {
        sessionStorage.setItem(submissionKey, key)
      } catch {
        /* optional recovery */
      }
      setRecoverKey(key)
      await finish(await createCompile(body, key))
    } catch (cause) {
      if (mounted.current) {
        const definite = isRejectedCompileSubmission(cause)
        if (definite && !restoredKey) {
          submission.current = null
          setRecoverKey(null)
          try {
            sessionStorage.removeItem(submissionKey)
          } catch {
            /* optional */
          }
        }
        // After reload the original args are unavailable. A rejection of the
        // reconstructed request does not prove the original was never created.
        if (restoredKey) submission.current = null
        setError(cause)
        setUncertain(!definite && !!submission.current)
      }
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  async function recover() {
    if (!recoverKey) return
    setBusy(true)
    try {
      await finish(await lookupSubmission(recoverKey))
    } catch (cause) {
      setError(cause)
    } finally {
      if (mounted.current) setBusy(false)
    }
  }
  useEffect(() => {
    if (error) document.getElementById('compile-error')?.focus()
  }, [error])
  const selected = skills.data?.find((s) => s.uri === draft.skill)
  return (
    <CompileShell title={t('new')}>
      {recoverKey && (
        <Button
          variant="outline"
          className="self-start"
          disabled={busy}
          onClick={() => void recover()}
        >
          {t('recover')}
        </Button>
      )}
      {fromTask && (
        <p className="text-sm text-muted-foreground">{t('rerunHint')}</p>
      )}
      {original.isError && (
        <CompileError
          error={original.error}
          retry={() => void original.refetch()}
        />
      )}
      {error != null && (
        <div id="compile-error" tabIndex={-1}>
          <CompileError error={error} />
        </div>
      )}
      {uncertain && (
        <p role="status" className="text-sm text-amber-700 dark:text-amber-400">
          {t('unknownSubmission')}
        </p>
      )}
      <form
        noValidate
        onSubmit={(e) => {
          e.preventDefault()
          void submit()
        }}
        className="w-full min-w-0 space-y-5"
      >
        <p className="text-sm text-muted-foreground">{t('formIntro')}</p>
        <fieldset
          disabled={busy || uncertain}
          className="space-y-6 rounded-xl border bg-card p-5 disabled:opacity-60 sm:p-6"
        >
          <section className="space-y-2">
            <label
              htmlFor="compile-skill"
              className="block text-sm font-medium"
            >
              {t('skill')} <span className="text-destructive">*</span>
            </label>
            <SkillPicker
              invalid={attempted && !!fieldErrors.skill}
              disabled={busy || uncertain || skills.isPending}
              loading={skills.isPending}
              value={draft.skill}
              skills={skills.data ?? []}
              onChange={(uri) => {
                update('skill', uri)
                setPreview(false)
              }}
            />
            {fieldError('skill')}
            <p className="text-xs text-muted-foreground">
              {t('skillLocationHint')}
            </p>
            {skills.isError && (
              <CompileError
                error={skills.error}
                retry={() => void skills.refetch()}
              />
            )}
            {skills.data?.length === 0 && (
              <Link to="/skills" className="text-sm text-primary underline">
                {t('noSkills')}
              </Link>
            )}
            {selected && (
              <p className="text-sm text-muted-foreground">
                {selected.description}
              </p>
            )}
            {draft.skill && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setPreview((v) => !v)}
              >
                {t('preview')}
              </Button>
            )}
            {preview && (
              <div className="max-h-80 overflow-auto rounded-lg border p-4">
                <pre className="whitespace-pre-wrap break-words text-xs">
                  {skillContent.data?.content || t('loading')}
                </pre>
                {skillContent.isError && (
                  <CompileError error={skillContent.error} />
                )}
              </div>
            )}
          </section>
          <section className="space-y-2 border-t pt-5">
            <label
              htmlFor="compile-sources"
              className="block text-sm font-medium"
            >
              {t('sources')} <span className="text-destructive">*</span>
            </label>
            <p id="source-hint" className="text-xs text-muted-foreground">
              {t('sourceHint')}
            </p>
            <div className="space-y-2">
              {sourceRows.map((uri, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Input
                    id={
                      index === 0
                        ? 'compile-sources'
                        : `compile-source-${index}`
                    }
                    aria-label={t('sourceNumber', { number: index + 1 })}
                    aria-invalid={attempted && !!fieldErrors.sources}
                    aria-describedby="source-hint error-sources"
                    placeholder={t('sourcePlaceholder')}
                    value={uri}
                    className="min-w-0 flex-1 font-mono text-xs"
                    onChange={(e) =>
                      update(
                        'sources',
                        sourceRows
                          .map((v, i) => (i === index ? e.target.value : v))
                          .join('\n'),
                      )
                    }
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label={t('removeSource', { uri: uri || index + 1 })}
                    onClick={() =>
                      update(
                        'sources',
                        sourceRows.filter((_, i) => i !== index).join('\n'),
                      )
                    }
                  >
                    <X className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
            {fieldError('sources')}
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setPicker('sources')}
              >
                <FolderOpen className="size-4" />
                {t('browse')}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() =>
                  update('sources', [...sourceRows, ''].join('\n'))
                }
              >
                <Plus className="size-4" />
                {t('addSource')}
              </Button>
            </div>
          </section>
          <section className="space-y-2">
            <label
              htmlFor="compile-target"
              className="block text-sm font-medium"
            >
              {t('target')} <span className="text-destructive">*</span>
            </label>
            <div className="flex gap-2">
              <Input
                id="compile-target"
                aria-invalid={attempted && !!fieldErrors.to}
                aria-describedby="target-hint error-to"
                className="min-w-0 flex-1 font-mono text-xs"
                required
                value={draft.to}
                onChange={(e) => update('to', e.target.value)}
                placeholder={t('targetPlaceholder')}
              />
              <Button
                type="button"
                variant="outline"
                onClick={() => setPicker('to')}
              >
                {t('browse')}
              </Button>
            </div>
            {fieldError('to')}
            <p id="target-hint" className="text-xs text-muted-foreground">
              {t('targetHint')}
            </p>
          </section>
          <section className="space-y-2">
            <label
              htmlFor="compile-instruction"
              className="block text-sm font-medium"
            >
              {t('instruction')}{' '}
              <span className="ml-2 font-normal text-muted-foreground">
                {t('optional')}
              </span>
            </label>
            <Textarea
              id="compile-instruction"
              rows={2}
              placeholder={t('instructionPlaceholder')}
              value={draft.instruction}
              onChange={(e) => update('instruction', e.target.value)}
            />
          </section>
          <details id="compile-advanced" className="border-t pt-4">
            <summary className="cursor-pointer rounded text-sm font-medium focus-visible:outline-2 focus-visible:outline-ring">
              {t('advanced')}
            </summary>
            <ArgsEditor value={args} onChange={setArgs} />
            <p className="mt-2 text-xs text-muted-foreground">
              {t('argsHint')}
            </p>
            {fieldError('args')}
          </details>
        </fieldset>
        <div className="sticky bottom-0 space-y-3 border-t bg-background/95 py-4 backdrop-blur">
          <p className="text-xs text-muted-foreground">
            {t(savingError ? 'storageFailed' : 'draft')}
          </p>
          <div className="flex flex-wrap justify-between gap-3">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                try {
                  if (!validate()) return
                  const body = request()
                  void navigator.clipboard
                    .writeText(compileCommand(body))
                    .then(() => toast.success(t('copied')))
                    .catch(setError)
                } catch (cause) {
                  setError(cause)
                }
              }}
            >
              {t('copyCommand')}
            </Button>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="ghost"
                disabled={busy || uncertain}
                onClick={() => {
                  setDraft(empty)
                  setArgs('')
                  setAttempted(false)
                  setError(null)
                  setPreview(false)
                  submission.current = null
                  setRecoverKey(null)
                  try {
                    sessionStorage.removeItem(submissionKey)
                  } catch {
                    /* optional */
                  }
                }}
              >
                {t('clear')}
              </Button>
              <Button type="submit" disabled={busy}>
                {t(busy ? 'submitting' : uncertain ? 'retry' : 'start')}
              </Button>
            </div>
          </div>
        </div>
      </form>
      <DirectoryPickerDialog
        identityScopeKey={identityScopeKey}
        userResourceRoot={userResourceRoot}
        open={!!picker}
        onOpenChange={(open) => {
          if (!open) setPicker(null)
        }}
        value={picker === 'to' && draft.to ? draft.to : userResourceRoot}
        onSelect={(uri) => {
          if (picker === 'to') update('to', uri)
          else
            update(
              'sources',
              [
                ...new Set([...draft.sources.split('\n').filter(Boolean), uri]),
              ].join('\n'),
            )
          setPicker(null)
        }}
      />
    </CompileShell>
  )
}
