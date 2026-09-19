import {
  RuntimeUserSelect,
  useRuntimeUserSelection,
} from '../../-components/runtime-user-select'
import { ReplyMode } from './reply-settings'
import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { QRCodeSVG } from 'qrcode.react'
import { Loader2Icon } from 'lucide-react'
import { Button } from '#/components/ui/button'
import { Input } from '#/components/ui/input'
import { createRandomUuid } from '#/lib/browser-crypto'
import { useAppConnection } from '#/hooks/use-app-connection'
import { getConnections } from '../../-api'
import { FeishuSetup } from './feishu-setup'
import { GroupSetup } from './group-setup'
import {
  currentOnboarding,
  getOnboarding,
  startOnboarding,
  updateOnboarding,
} from './api'
import type { OnboardingRun } from './api'
import type { Connection } from '../../-api'

const STEP_LABELS = ['choose', 'scan', 'addGroup'] as const
const finished = new Set([
  'ready',
  'failed',
  'expired',
  'interrupted',
  'cancelled',
])
const cancellable = new Set([
  'initializing',
  'waiting_for_scan',
  'scanned',
  'expired',
])

export function FeishuConnect(props: {
  connection?: Connection
  onChange: (connection: Connection) => void
  onClose: () => void
}) {
  const [manual, setManual] = useState<Connection | boolean>(false)
  if (manual || (props.connection && props.connection.setup_mode !== 'qr')) {
    return (
      <FeishuSetup
        {...props}
        connection={typeof manual === 'object' ? manual : props.connection}
      />
    )
  }
  return <ScanSetup {...props} onManual={(value) => setManual(value ?? true)} />
}

function ScanSetup({
  connection,
  onChange,
  onClose,
  onManual,
}: {
  connection?: Connection
  onChange: (connection: Connection) => void
  onClose: () => void
  onManual: (connection?: Connection) => void
}) {
  const { t } = useTranslation('vikingbot')
  const { identityScopeKey: scope } = useAppConnection()
  const client = useQueryClient()
  const [requireMention, setRequireMention] = useState(true)
  const [name, setName] = useState('VikingBot')
  const [jobId, setJobId] = useState(connection?.onboarding_id)
  const busy = useRef(false)
  const requestId = useRef(createRandomUuid())
  const currentKey = ['vikingbot', scope, 'onboarding', 'current']
  const current = useQuery({
    queryKey: currentKey,
    queryFn: currentOnboarding,
    enabled: !jobId,
    retry: false,
    staleTime: 0,
  })
  const id = jobId ?? current.data?.id
  const jobKey = ['vikingbot', scope, 'onboarding', id]
  const job = useQuery({
    queryKey: jobKey,
    queryFn: () => getOnboarding(id!),
    enabled: Boolean(id),
    retry: false,
    refetchInterval: (query) =>
      query.state.data && finished.has(query.state.data.state) ? false : 1500,
  })
  const run = job.data ?? (current.data?.id === id ? current.data : undefined)
  const userSelection = useRuntimeUserSelection(!id)
  const { selectedUser } = userSelection
  const connections = useQuery({
    queryKey: ['vikingbot', scope, 'connections'],
    queryFn: getConnections,
    enabled: Boolean(run?.connection_id),
    refetchInterval: run?.state === 'ready' ? 4000 : false,
  })
  const linked =
    connection ??
    connections.data?.find((item) => item.id === run?.connection_id)
  const mutation = useMutation({
    mutationFn: (action: 'start' | 'retry' | 'cancel' | 'manual') =>
      action === 'start'
        ? startOnboarding({
            user_id: selectedUser,
            settings: { thread_require_mention: requireMention },
            name,
            request_id: requestId.current,
          })
        : updateOnboarding(id!, action),
    onSuccess: (value: OnboardingRun, action) => {
      if (action === 'manual') {
        client.setQueryData(currentKey, null)
        onManual(linked)
        return
      }
      if (value.state === 'cancelled') {
        client.setQueryData(currentKey, null)
        setJobId(undefined)
        requestId.current = createRandomUuid()
      } else {
        client.setQueryData(['vikingbot', scope, 'onboarding', value.id], value)
        setJobId(value.id)
      }
    },
  })
  function submit(action: 'start' | 'retry' | 'cancel' | 'manual') {
    if (busy.current) return
    busy.current = true
    mutation.mutate(action, {
      onSettled: () => {
        busy.current = false
      },
    })
  }
  const error =
    mutation.error || current.error || job.error || connections.error
  const step = run?.state === 'ready' ? 2 : id ? 1 : 0
  return (
    <section className="mx-auto w-full min-w-0 max-w-4xl space-y-8 px-6 py-8 sm:px-8 lg:px-12 lg:py-10">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-semibold">{t('setupTitle')}</h2>
          <p className="mt-2 text-sm text-muted-foreground">{t('qr.intro')}</p>
        </div>
        <Button variant="ghost" onClick={onClose}>
          {t('qr.close')}
        </Button>
      </div>
      <ol className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
        {STEP_LABELS.map((label, index) => (
          <li
            key={label}
            aria-current={step === index ? 'step' : undefined}
            className={`rounded-lg px-4 py-3 ${step === index ? 'bg-primary/10 font-medium text-primary' : 'text-muted-foreground'}`}
          >
            {index + 1}. {t(`qr.${label}`)}
          </li>
        ))}
      </ol>
      <div className="rounded-xl border p-6 sm:p-8">
        {!id && current.isPending ? (
          <p role="status">{t('loading')}</p>
        ) : !id && !current.error ? (
          <div className="space-y-6">
            <RuntimeUserSelect
              selection={userSelection}
              disabled={mutation.isPending}
            />
            <ReplyMode
              value={requireMention}
              onChange={setRequireMention}
              disabled={mutation.isPending}
            />
            <label className="grid gap-3 text-sm">
              <span>{t('qr.botName')}</span>
              <Input
                className="h-11 px-4"
                value={name}
                maxLength={50}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <p className="text-xs leading-6 text-muted-foreground">
              {t('qr.consent')}
            </p>
            <Button
              disabled={
                !selectedUser ||
                !name.trim() ||
                mutation.isPending ||
                current.isPending
              }
              onClick={() => submit('start')}
            >
              {mutation.isPending && (
                <Loader2Icon className="size-4 animate-spin" />
              )}
              {t('qr.start')}
            </Button>
          </div>
        ) : run?.state === 'ready' && linked ? (
          <GroupSetup
            connection={linked}
            onChange={onChange}
            onClose={onClose}
          />
        ) : (
          <div className="space-y-4">
            <p role="status" className="font-medium">
              {run ? t(`qr.states.${run.state}`) : t('loading')}
            </p>
            {run?.qr && !finished.has(run.state) && (
              <div className="w-fit rounded-xl bg-white p-4">
                <QRCodeSVG
                  value={run.qr}
                  size={208}
                  marginSize={2}
                  title={t('qr.scan')}
                />
              </div>
            )}
            {run?.qr && (
              <p className="text-sm text-muted-foreground">
                {t('qr.scanHint')}
              </p>
            )}
            {run && (
              <p className="text-sm text-muted-foreground">
                {t('runtimeUser')}: {run.user_id}
              </p>
            )}
            {run?.owner && (
              <p className="text-sm text-muted-foreground">
                {run.owner.user_name} · {run.owner.tenant_name}
              </p>
            )}
            {run?.state === 'awaiting_approval' && (
              <p className="text-sm text-muted-foreground">
                {t('qr.approvalHint')}
              </p>
            )}
            {run?.error && (
              <p role="alert" className="text-sm text-destructive">
                {t(`qr.errors.${run.error}`, {
                  defaultValue: t('qr.errors.setup_failed'),
                })}
              </p>
            )}
            {run?.app_id && (
              <a
                className="inline-block text-sm underline"
                target="_blank"
                rel="noreferrer"
                href={`https://open.feishu.cn/app/${encodeURIComponent(run.app_id)}`}
              >
                {t('openPlatform')}
              </a>
            )}
            <div className="flex flex-wrap gap-3">
              {run &&
                ['failed', 'expired', 'interrupted'].includes(run.state) && (
                  <Button
                    variant="outline"
                    disabled={mutation.isPending}
                    onClick={() => submit('manual')}
                  >
                    {t('qr.manualRecovery')}
                  </Button>
                )}
              {run?.can_retry && (
                <Button
                  disabled={mutation.isPending}
                  onClick={() => submit('retry')}
                >
                  {t('qr.retryScan')}
                </Button>
              )}
              {run && cancellable.has(run.state) && (
                <Button
                  variant="outline"
                  disabled={mutation.isPending}
                  onClick={() => submit('cancel')}
                >
                  {t('qr.cancel')}
                </Button>
              )}
            </div>
          </div>
        )}
        {error && (
          <div className="mt-4 space-y-2">
            <p role="alert" className="text-sm text-destructive">
              {t('error', { error: error.message })}
            </p>
            <Button
              variant="outline"
              onClick={() => {
                void current.refetch()
                if (id) void job.refetch()
              }}
            >
              {t('retry')}
            </Button>
          </div>
        )}
      </div>
      {!id && (
        <Button variant="link" onClick={() => onManual()}>
          {t('qr.manual')}
        </Button>
      )}
    </section>
  )
}
