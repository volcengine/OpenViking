import { copyTextToClipboard } from '#/shared/lib/clipboard'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { ArgsEditor } from '#/features/workbench/compile/components/args-editor'
import {
  CapabilityNotice,
  useCompileCapability,
} from '#/features/workbench/compile/components/capability-notice'
import { CompileLocations } from '#/features/workbench/compile/components/compile-locations'
import {
  CompileError,
  CompileShell,
} from '#/features/workbench/compile/components/shared'
import { SkillPicker } from '#/features/workbench/compile/components/skill-picker'
import type {
  CompileRequest,
  CompileTask,
} from '#/features/workbench/compile/lib/api'
import {
  createCompile,
  fetchCompileTask,
  lookupSubmission,
} from '#/features/workbench/compile/lib/api'
import {
  compileCommand,
  parseArgs,
} from '#/features/workbench/compile/lib/commands'
import { isRejectedCompileSubmission } from '#/features/workbench/compile/lib/errors'
import {
  clearCompileHandoff,
  readCompileHandoff,
} from '#/features/workbench/compile/lib/handoff'
import {
  locationLabel,
  sourceUris,
  targetError,
} from '#/features/workbench/compile/lib/locations'
import {
  skillGuide,
  useCompileSkills,
} from '#/features/workbench/compile/lib/skills'
import {
  fetchFileContent,
  fetchFsStat,
} from '#/features/workbench/resources/lib/api'
import { isOvClientError } from '#/shared/api/ov-client/index'
import { createRandomUuid } from '#/shared/lib/browser-crypto'
import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '#/shared/ui/dialog'
import { Textarea } from '#/shared/ui/textarea'
import { useQuery } from '@tanstack/react-query'
import { Link, useBlocker, useNavigate } from '@tanstack/react-router'
import { EyeIcon } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

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
export function CompileForm({
  fromTask,
  sourceUri,
}: {
  fromTask?: string
  sourceUri?: string
}) {
  const { t } = useTranslation('compile'),
    navigate = useNavigate()
  const { identityScopeKey, connection } = useAppConnection()
  const userResourceRoot = `viking://user/${connection.userId || 'default'}/resources/`
  const draftKey = `compile-draft:${identityScopeKey}`,
    submissionKey = `compile-submission:${identityScopeKey}`
  const [handoff] = useState(() => readCompileHandoff(identityScopeKey))
  const [recoverKey, setRecoverKey] = useState(() => {
    try {
      return sessionStorage.getItem(submissionKey)
    } catch {
      return null
    }
  })
  const [savedDraft] = useState(() => readDraft(draftKey))
  const [draftStarted, setDraftStarted] = useState(
    () => !!(recoverKey || handoff || fromTask || sourceUri),
  )
  useEffect(() => {
    clearCompileHandoff()
  }, [])
  const [draft, setDraft] = useState(() =>
    recoverKey
      ? savedDraft
      : handoff
        ? {
            skill: handoff.skill || '',
            sources: handoff.from?.join('\n') || '',
            to: handoff.to || '',
            instruction: handoff.instruction || '',
          }
        : fromTask
          ? savedDraft
          : {
              ...empty,
              sources: sourceUri || userResourceRoot.replace(/\/$/, ''),
            },
  )
  const [args, setArgs] = useState(
      !recoverKey && handoff?.args ? JSON.stringify(handoff.args, null, 2) : '',
    ),
    [preview, setPreview] = useState(false)
  const [locationRevision, setLocationRevision] = useState(0)
  const capability = useCompileCapability()
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
    if (completed.current || !draftStarted) return
    try {
      localStorage.setItem(draftKey, JSON.stringify(draft))
      setSavingError(false)
    } catch {
      setSavingError(true)
    }
  }, [draft, draftKey, draftStarted])
  const skills = useCompileSkills()
  const original = useQuery({
    queryKey: ['compile-task', identityScopeKey, fromTask],
    queryFn: ({ signal }) => fetchCompileTask(fromTask!, signal),
    enabled: !!fromTask,
  })
  useEffect(() => {
    const originalRequest = original.data?.meta?.request
    if (!originalRequest || filled.current || recoverKey) return
    filled.current = true
    if (
      Object.values(readDraft(draftKey)).some(Boolean) &&
      !window.confirm(t('selectDraft'))
    )
      return
    setLocationRevision((value) => value + 1)
    setDraft({
      skill: originalRequest.skill,
      sources: originalRequest.from.join('\n'),
      to: originalRequest.to,
      instruction: originalRequest.instruction || '',
    })
    setArgs(
      originalRequest.args ? JSON.stringify(originalRequest.args, null, 2) : '',
    )
  }, [original.data, draftKey, recoverKey, t])
  const skillContent = useQuery({
    queryKey: ['compile-skill-content', identityScopeKey, draft.skill],
    queryFn: () =>
      fetchFileContent(`${draft.skill.replace(/\/$/, '')}/SKILL.md`),
    enabled: preview && !!draft.skill,
  })
  function update(key: keyof Draft, value: string) {
    setDraftStarted(true)
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
    to: targetError(draft.to.trim()) ? t(targetError(draft.to.trim())!) : '',
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
  function isCurrent() {
    return mounted.current
  }
  async function finish(task: CompileTask) {
    if (!isCurrent()) return
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
        if (!isCurrent()) return
        const submittedAt = Number(restoredKey.split(':')[0])
        if (
          !Number.isFinite(submittedAt) ||
          Date.now() - submittedAt > 86400000
        )
          throw new Error(t('expiredSubmission'))
      }
      if (!submission.current && !validate()) return
      const checked = await capability.refetch()
      if (!isCurrent() || checked.isError || !checked.data?.can_create) return
      if (!submission.current && !restoredKey) {
        const entries = await Promise.all(
          sourceUris(draft.sources).map((uri) =>
            fetchFsStat(uri, { throwOnError: true }),
          ),
        )
        if (!isCurrent()) return
        if (entries.some((entry) => !entry.isDir))
          throw new Error(t('sourceNotDirectory'))
        try {
          const destination = await fetchFsStat(draft.to.trim(), {
            throwOnError: true,
          })
          if (!destination.isDir) throw new Error(t('targetNotDirectory'))
        } catch (cause) {
          // Only a confirmed missing directory is safe to create during execution.
          if ((cause as { code?: string }).code !== 'NOT_FOUND') throw cause
        }
        if (!isCurrent()) return
      }
      const body = submission.current?.request || request()
      if (!submission.current)
        submission.current = {
          key: recoverKey || `${Date.now()}:${createRandomUuid()}`,
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
  const selected = skills.data.find((s) => s.uri === draft.skill)
  const guide = selected ? skillGuide(selected, t) : null
  const targetLabel = locationLabel(draft.to, userResourceRoot)
  const skillGroups = [
    ['builtinSkills', skills.builtin],
    ['mySkills', skills.mine],
    ['sharedSkills', skills.shared],
  ] as const
  return (
    <CompileShell title={t('new')}>
      <CapabilityNotice capability={capability} />
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
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-muted-foreground">{t('formIntro')}</p>
          {!draftStarted && Object.values(savedDraft).some(Boolean) && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              disabled={busy || uncertain}
              onClick={() => {
                setDraft(savedDraft)
                setDraftStarted(true)
                setAttempted(false)
                setError(null)
                setLocationRevision((value) => value + 1)
              }}
            >
              {t('restoreDraft')}
            </Button>
          )}
        </div>
        <fieldset
          disabled={busy || uncertain}
          className="space-y-6 rounded-xl border bg-card p-5 disabled:opacity-60 sm:p-6"
        >
          <section className="space-y-2">
            <div className="flex items-center justify-between gap-3">
              <label
                htmlFor="compile-skill"
                className="block text-sm font-medium"
              >
                {t('skill')} <span className="text-destructive">*</span>
              </label>
              {draft.skill && (
                <Dialog open={preview} onOpenChange={setPreview}>
                  <DialogTrigger
                    render={<Button type="button" variant="ghost" size="sm" />}
                  >
                    <EyeIcon className="size-4" />
                    {t('preview')}
                  </DialogTrigger>
                  <DialogContent className="flex max-h-[80dvh] min-w-0 flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
                    <DialogHeader className="shrink-0 border-b py-5 pl-6 pr-14">
                      <DialogTitle className="break-words text-lg leading-snug">
                        {guide?.name || selected?.name || t('preview')}
                      </DialogTitle>
                      <DialogDescription className="break-all text-xs">
                        {draft.skill}
                      </DialogDescription>
                    </DialogHeader>
                    <div className="min-h-0 overflow-auto overscroll-contain px-6 py-5">
                      {skillContent.isPending ? (
                        <p role="status" className="text-muted-foreground">
                          {t('loading')}
                        </p>
                      ) : skillContent.isError ? (
                        <CompileError
                          error={skillContent.error}
                          retry={() => void skillContent.refetch()}
                        />
                      ) : skillContent.data.content ? (
                        <pre className="whitespace-pre-wrap break-words text-xs leading-relaxed">
                          {skillContent.data.content}
                        </pre>
                      ) : (
                        <p className="text-muted-foreground">
                          {t('skillContentEmpty')}
                        </p>
                      )}
                    </div>
                  </DialogContent>
                </Dialog>
              )}
            </div>
            <SkillPicker
              invalid={attempted && !!fieldErrors.skill}
              disabled={
                busy ||
                uncertain ||
                (skills.builtin.isPending &&
                  skills.mine.isPending &&
                  skills.shared.isPending)
              }
              loading={
                skills.builtin.isPending ||
                skills.mine.isPending ||
                skills.shared.isPending
              }
              value={draft.skill}
              skills={skills.data}
              onChange={(uri) => {
                update('skill', uri)
                setPreview(false)
              }}
            />
            {fieldError('skill')}
            <p className="text-xs text-muted-foreground">
              {t('skillLocationHint')}
            </p>
            {skillGroups.map(
              ([label, query]) =>
                query.isError && (
                  <div key={label} className="space-y-2">
                    <p className="text-sm">
                      {t('skillGroupFailed', { group: t(label) })}
                    </p>
                    <CompileError
                      error={query.error}
                      retry={() => void query.refetch()}
                    />
                  </div>
                ),
            )}
            {!skills.builtin.isPending &&
              !skills.mine.isPending &&
              !skills.shared.isPending &&
              !skills.builtin.isError &&
              !skills.mine.isError &&
              !skills.shared.isError &&
              !skills.data.length && (
                <p className="text-sm text-muted-foreground">
                  {t('noSkills')}{' '}
                  <Link to="/skills" className="text-primary underline">
                    {t('manageSkills')}
                  </Link>
                </p>
              )}
            {selected?.metadataError && (
              <p className="text-xs text-muted-foreground">
                {t('skillMetadataFailed')}{' '}
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => void skills.builtin.refetch()}
                >
                  {t('retry')}
                </Button>
              </p>
            )}
          </section>
          <CompileLocations
            key={locationRevision}
            sources={draft.sources}
            to={draft.to}
            onSources={(value) => update('sources', value)}
            onTarget={(value) => update('to', value)}
            attempted={attempted}
            sourceError={fieldErrors.sources}
            destinationError={fieldErrors.to}
          />
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
            <ArgsEditor
              value={args}
              onChange={(value) => {
                setDraftStarted(true)
                setArgs(value)
              }}
            />
            <p className="mt-2 text-xs text-muted-foreground">
              {t('argsHint')}
            </p>
            {fieldError('args')}
          </details>
        </fieldset>
        <div className="sticky bottom-0 space-y-3 border-t bg-background py-4">
          {draft.skill && sourceUris(draft.sources).length > 0 && draft.to && (
            <p className="break-words text-sm">
              {t('submissionSummary', {
                skill:
                  guide?.name || selected?.name || draft.skill.split('/').pop(),
                count: sourceUris(draft.sources).length,
                space: t(targetLabel.scope),
                folder: targetLabel.path || t('spaceRoot'),
              })}
            </p>
          )}
          {draftStarted && (
            <p className="text-xs text-muted-foreground">
              {t(savingError ? 'storageFailed' : 'draft')}
            </p>
          )}
          <div className="flex flex-wrap justify-between gap-3">
            <Button
              type="button"
              variant="outline"
              onClick={() => {
                try {
                  if (!validate()) return
                  const body = request()
                  void copyTextToClipboard(compileCommand(body))
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
                  setDraftStarted(true)
                  setDraft(empty)
                  setLocationRevision((value) => value + 1)
                  setArgs('')
                  setAttempted(false)
                  setError(null)
                  setPreview(false)
                  submission.current = null
                  filled.current = true
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
              <Button
                type="submit"
                disabled={
                  busy ||
                  (!recoverKey &&
                    !uncertain &&
                    (capability.isError || !capability.data?.can_create))
                }
              >
                {t(busy ? 'submitting' : uncertain ? 'retry' : 'start')}
              </Button>
            </div>
          </div>
        </div>
      </form>
    </CompileShell>
  )
}
