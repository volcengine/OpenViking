import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { ContextLink } from '#/features/workbench/compile/components/context-link'
import {
  defaultSchedule,
  ScheduleFields,
} from '#/features/workbench/compile/components/schedule-fields'
import {
  CompileError,
  CompileLoading,
} from '#/features/workbench/compile/components/shared'
import {
  createCompile,
  fetchCompileConfig,
  updateCompileConfig,
} from '#/features/workbench/compile/lib/api'
import type {
  CompileConfigSummary,
  fetchCompileConfigs,
  UpdateSchedule,
} from '#/features/workbench/compile/lib/api'
import { Button } from '#/shared/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/shared/ui/dialog'
import { Switch } from '#/shared/ui/switch'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { InfiniteData } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { LoaderCircleIcon, PencilIcon } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { createRandomUuid } from '#/shared/lib/browser-crypto'

const SCHEDULE_TIME_ZONE = 'Asia/Shanghai'
const ACCEPTANCE_TIMES = ['task_first_create_time', 'last_run_time'] as const
type ConfigPage = Awaited<ReturnType<typeof fetchCompileConfigs>>

export function CompileScheduleTarget({
  target,
}: {
  target: CompileConfigSummary
}) {
  const { t, i18n } = useTranslation('compile')
  const { identityScopeKey } = useAppConnection()
  const client = useQueryClient()
  const navigate = useNavigate()
  const runKey = useRef<string | null>(null)
  const run = useMutation({
    mutationFn: () => {
      runKey.current ??= `${Date.now()}:${createRandomUuid()}`
      return createCompile(
        { compile_name: target.compile_name },
        runKey.current,
      )
    },
    onSuccess: async (task) => {
      runKey.current = null
      await navigate({
        to: '/compile/tasks/$taskId',
        params: { taskId: task.task_id },
      })
    },
  })
  const switchId = useId()
  const [editing, setEditing] = useState(false)
  const pendingRead = useRef<AbortController | null>(null)
  useEffect(() => () => pendingRead.current?.abort(), [])
  const mutation = useMutation({
    mutationFn: async (schedule: UpdateSchedule | null) => {
      // Read fresh source/Skill settings so a list action cannot restore stale ones.
      const controller = new AbortController()
      pendingRead.current = controller
      const config = await fetchCompileConfig(
        target.compile_name,
        controller.signal,
      )
      // The active connection may have changed while reading the configuration.
      controller.signal.throwIfAborted()
      return updateCompileConfig({
        compile_name: target.compile_name,
        to: config.to,
        args: config.args,
        from: config.from,
        skill: config.skill,
        instruction: config.instruction,
        auto_update: schedule !== null,
        update_schedule: schedule,
      })
    },
    onSuccess: async (config) => {
      const listKey = ['compile-configs', identityScopeKey]
      await client.cancelQueries({ queryKey: listKey })
      client.setQueryData<InfiniteData<ConfigPage>>(
        listKey,
        (data) =>
          data && {
            ...data,
            pages: data.pages.map((page) => ({
              ...page,
              configs: page.configs.map((item) =>
                item.compile_name === target.compile_name
                  ? { ...item, ...config }
                  : item,
              ),
            })),
          },
      )
      setEditing(false)
      toast.success(
        t(config.auto_update ? 'schedule.saved' : 'schedule.turnedOff'),
      )
      await Promise.all([
        client.invalidateQueries({ queryKey: listKey }),
        client.invalidateQueries({
          queryKey: ['compile-config', identityScopeKey, target.compile_name],
          refetchType: 'none',
        }),
      ])
    },
  })
  const openEditor = () => {
    mutation.reset()
    setEditing(true)
  }
  const schedule = target.update_schedule
  const time = schedule
    ? `${String(schedule.hour).padStart(2, '0')}:${String(schedule.minute).padStart(2, '0')}`
    : ''
  const nextRun = target.next_triggered_slot
    ? new Date(target.next_triggered_slot)
    : null
  const validNextRun = nextRun && !Number.isNaN(nextRun.getTime())

  return (
    <li
      className="space-y-4 border-b p-5 last:border-b-0"
      aria-busy={mutation.isPending}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h2 className="min-w-0 flex-1 break-words text-sm font-medium [overflow-wrap:anywhere]">
          <ContextLink uri={target.to}>{target.to}</ContextLink>
          <span className="mt-1 block text-xs font-normal text-muted-foreground">
            {target.compile_name}
          </span>
        </h2>
        <Button disabled={run.isPending} onClick={() => run.mutate()}>
          {t('start')}
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={mutation.isPending}
          aria-label={`${t('schedule.edit')}: ${target.to}`}
          onClick={() =>
            void navigate({
              to: '/compile/new',
              search: { compileName: target.compile_name },
            })
          }
        >
          {t('schedule.edit')}
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor={switchId} className="text-sm font-medium">
          {t('schedule.autoUpdate')}
          <span className="sr-only">{`: ${target.to}`}</span>
        </label>
        <Switch
          id={switchId}
          aria-label={`${t('schedule.autoUpdate')}: ${target.to}`}
          checked={target.auto_update}
          disabled={mutation.isPending}
          onCheckedChange={(checked) => {
            if (checked) openEditor()
            else mutation.mutate(null)
          }}
        />
        <span
          className="flex items-center gap-2 text-xs text-muted-foreground"
          role="status"
        >
          {mutation.isPending && (
            <LoaderCircleIcon className="size-3 animate-spin" />
          )}
          {t(
            mutation.isPending
              ? 'schedule.saving'
              : target.auto_update
                ? 'schedule.active'
                : 'schedule.disabled',
          )}
        </span>
      </div>
      <dl className="grid gap-4 text-sm sm:grid-cols-3">
        <div className="space-y-1">
          <dt className="text-xs text-muted-foreground">
            {t('schedule.timing')}
          </dt>
          <dd className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span>
              {schedule
                ? schedule.frequency === 'daily'
                  ? t('schedule.dailyAt', { time })
                  : t('schedule.weeklyAt', {
                      time,
                      weekday: t(`schedule.weekdays.${schedule.weekday}`),
                    })
                : t('schedule.noSchedule')}
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-auto px-1 py-0.5 text-xs text-muted-foreground"
              disabled={mutation.isPending}
              aria-label={`${t(target.auto_update ? 'schedule.adjust' : 'schedule.setUp')}: ${target.to}`}
              onClick={openEditor}
            >
              <PencilIcon className="size-3" />
              {t(target.auto_update ? 'schedule.adjust' : 'schedule.setUp')}
            </Button>
          </dd>
        </div>
        <div className="space-y-1">
          <dt className="text-xs text-muted-foreground">
            {t('schedule.nextRun')}
          </dt>
          <dd>
            {validNextRun ? (
              <time dateTime={nextRun.toISOString()}>
                {nextRun.toLocaleString(i18n.language, {
                  timeZone: SCHEDULE_TIME_ZONE,
                })}
              </time>
            ) : (
              t('schedule.notScheduled')
            )}
          </dd>
        </div>
        {ACCEPTANCE_TIMES.map((field) => (
          <div className="space-y-1" key={field}>
            <dt className="text-xs text-muted-foreground">{t(field)}</dt>
            <dd>
              {target[field] === null
                ? t('neverRun')
                : new Date(target[field] * 1000).toLocaleString(i18n.language)}
            </dd>
          </div>
        ))}
      </dl>
      {run.isError && (
        <CompileError error={run.error} retry={() => run.mutate()} />
      )}
      {!editing && mutation.isError && (
        <CompileError
          error={mutation.error}
          retry={() => mutation.mutate(null)}
        />
      )}
      {editing && (
        <ScheduleDialog
          target={target}
          pending={mutation.isPending}
          error={mutation.isError ? mutation.error : null}
          onClose={() => {
            if (!mutation.isPending) {
              mutation.reset()
              setEditing(false)
            }
          }}
          onSave={(value) => mutation.mutate(value)}
        />
      )}
    </li>
  )
}

function ScheduleDialog({
  target,
  pending,
  error,
  onClose,
  onSave,
}: {
  target: CompileConfigSummary
  pending: boolean
  error: unknown
  onClose: () => void
  onSave: (schedule: UpdateSchedule) => void
}) {
  const { t } = useTranslation('compile')
  const { identityScopeKey } = useAppConnection()
  const [schedule, setSchedule] = useState<UpdateSchedule>(defaultSchedule)
  const [filled, setFilled] = useState(false)
  const config = useQuery({
    queryKey: ['compile-config', identityScopeKey, target.compile_name],
    queryFn: ({ signal }) => fetchCompileConfig(target.compile_name, signal),
    staleTime: 0,
  })
  useEffect(() => {
    if (!config.isSuccess || !config.isFetchedAfterMount || filled) return
    setSchedule(config.data.update_schedule ?? defaultSchedule)
    setFilled(true)
  }, [config.data, config.isSuccess, config.isFetchedAfterMount, filled])

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
    >
      <DialogContent showCloseButton={!pending}>
        <DialogHeader>
          <DialogTitle>
            {t(target.auto_update ? 'schedule.adjust' : 'schedule.turnOn')}
          </DialogTitle>
          <DialogDescription className="break-all">
            {target.to}
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-5"
          onSubmit={(event) => {
            event.preventDefault()
            if (filled && !pending && !config.isError) onSave(schedule)
          }}
        >
          {!filled && !config.isError && <CompileLoading />}
          {config.isError && (
            <CompileError
              error={config.error}
              retry={() => void config.refetch()}
            />
          )}
          {filled && (
            <fieldset disabled={pending}>
              <ScheduleFields
                enabled
                schedule={schedule}
                onEnabled={() => {}}
                onChange={setSchedule}
                showToggle={false}
              />
            </fieldset>
          )}
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t('schedule.disableHint')}
          </p>
          {error != null && <CompileError error={error} />}
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={pending}
              onClick={onClose}
            >
              {t('cancel')}
            </Button>
            <Button
              type="submit"
              disabled={!filled || pending || config.isError}
            >
              {pending && <LoaderCircleIcon className="size-4 animate-spin" />}
              {t(
                pending
                  ? 'schedule.saving'
                  : target.auto_update
                    ? 'schedule.save'
                    : 'schedule.turnOn',
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
