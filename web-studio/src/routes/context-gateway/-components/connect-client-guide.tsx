import { Fragment } from 'react'
import { Link } from '@tanstack/react-router'
import {
  InfoIcon,
  MessagesSquareIcon,
  PlusIcon,
  TriangleAlertIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import { cn } from '#/lib/utils'

import type { Protocol, Upstream } from '../-lib/api'
import {
  CLIENT_IDS,
  KEY_PLACEHOLDER,
  MODEL_PLACEHOLDER,
  SESSION_PLACEHOLDER,
  activeProtocols,
  clientSnippets,
  defaultProtocol,
  protocolChoices,
  servingUpstreams,
} from '../-lib/client-guides'
import type { ClientId } from '../-lib/client-guides'
import { kindLabel, protocolLabel } from '../-lib/localize'
import { NEW_ID } from '../-lib/search'
import { CodeBlock } from './code-block'
import { CodeText } from './connect-text'
import { Notice } from './notice'
import { ProtocolBadge } from './status-badges'

/**
 * Setup steps per client, in order. Each id names its text under
 * `connect.clients.<client>.steps` and shows the snippet with the same id.
 */
export const CLIENT_STEPS: Record<ClientId, string[]> = {
  'claude-code': ['env', 'start'],
  codex: ['config', 'key', 'start'],
  chat: ['settings', 'python', 'curl'],
  'open-webui': ['connection', 'headers', 'env'],
  opencode: ['config', 'key', 'start'],
  pi: ['config', 'key'],
  dsh: ['config', 'key', 'start'],
  ark: ['endpoints', 'python'],
}

/** Caveats per client under `connect.clients.<client>.notes`. */
const CLIENT_NOTES: Record<ClientId, string[]> = {
  'claude-code': ['address', 'hints', 'models', 'subscription'],
  codex: ['websocket', 'metadata', 'login'],
  chat: ['streaming', 'otherApis', 'models'],
  'open-webui': ['sharedMemory', 'tasks'],
  opencode: ['plugin'],
  pi: ['extension'],
  dsh: ['webUi', 'oneProtocol', 'plugin'],
  ark: ['routing'],
}

/** Placeholders a snippet may contain, with their explanation key. */
const PLACEHOLDERS = [
  [KEY_PLACEHOLDER, 'key'],
  [MODEL_PLACEHOLDER, 'model'],
  [SESSION_PLACEHOLDER, 'session'],
] as const

const MAX_LISTED_UPSTREAMS = 3

type ClientGuideProps = {
  client: ClientId
  /** Protocol from the URL; ignored unless the client supports it. */
  protocol?: Protocol
  /** Gateway address the snippets point at. */
  baseUrl: string
  /** Configured upstreams; leave undefined while loading so no warning shows. */
  upstreams?: Upstream[]
}

/** Client picker (deep-linked through `?client=`) and the selected client's setup. */
export function ClientGuide({
  client,
  protocol,
  baseUrl,
  upstreams,
}: ClientGuideProps) {
  const { t } = useTranslation('contextGateway')
  return (
    <Card className="gap-0 py-0">
      <CardHeader className="border-b px-5 py-4">
        <CardTitle role="heading" aria-level={3}>
          {t('connect.guide.title')}
        </CardTitle>
        <CardDescription>{t('connect.guide.description')}</CardDescription>
      </CardHeader>
      <div className="grid min-w-0 lg:grid-cols-[14rem_minmax(0,1fr)]">
        <nav
          aria-label={t('connect.guide.clients')}
          className="flex min-w-0 gap-1 overflow-x-auto border-b p-2 lg:flex-col lg:overflow-visible lg:border-r lg:border-b-0"
        >
          {CLIENT_IDS.map((id) => {
            const active = id === client
            const missing =
              upstreams !== undefined &&
              servingUpstreams(id, upstreams).length === 0
            return (
              <Link
                key={id}
                to="/context-gateway/connect"
                search={{ client: id }}
                replace
                resetScroll={false}
                aria-current={active ? 'page' : undefined}
                className={cn(
                  'flex shrink-0 items-center justify-between gap-2 rounded-md px-3 py-2 text-sm whitespace-nowrap transition-colors lg:whitespace-normal hover:bg-muted/60 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
                  active
                    ? 'bg-muted font-medium text-foreground'
                    : 'text-muted-foreground',
                )}
              >
                {t(`connect.clients.${id}.name`)}
                {missing ? (
                  <span
                    className="text-amber-600 dark:text-amber-400"
                    title={t('connect.guide.noUpstream.hint')}
                  >
                    <TriangleAlertIcon aria-hidden className="size-3.5" />
                    <span className="sr-only">
                      {t('connect.guide.noUpstream.hint')}
                    </span>
                  </span>
                ) : null}
              </Link>
            )
          })}
        </nav>
        <ClientPanel
          client={client}
          protocol={protocol}
          baseUrl={baseUrl}
          upstreams={upstreams}
        />
      </div>
    </Card>
  )
}

function ClientPanel({
  client,
  protocol,
  baseUrl,
  upstreams,
}: ClientGuideProps) {
  const { t } = useTranslation('contextGateway')
  // Without a valid pick, start from a protocol an upstream already serves.
  const picked =
    protocol && protocolChoices(client).includes(protocol)
      ? protocol
      : defaultProtocol(client, upstreams)
  const snippets = clientSnippets(client, { baseUrl, protocol: picked })
  const placeholders = PLACEHOLDERS.filter(([token]) =>
    snippets.some((snippet) => snippet.code.includes(token)),
  )
  const prefix = `connect.clients.${client}`
  const headingId = `connect-client-${client}`
  // Values some client notes refer to, so they match labels elsewhere.
  const values = {
    kind: kindLabel(t, 'user'),
    section: t('connect.identity.title'),
  }

  return (
    <section aria-labelledby={headingId} className="grid min-w-0 gap-5 p-5">
      <div className="grid gap-1">
        <h4 id={headingId} className="text-base font-semibold tracking-tight">
          {t(`${prefix}.name`)}
        </h4>
        <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
          <CodeText text={t(`${prefix}.intro`)} />
        </p>
      </div>

      <ProtocolRequirement
        client={client}
        protocol={picked}
        upstreams={upstreams}
      />

      {placeholders.length ? (
        <div className="grid gap-1.5 rounded-md border bg-muted/20 px-3 py-2.5 text-sm">
          <p className="font-medium">{t('connect.guide.placeholders')}</p>
          <dl className="grid gap-x-3 gap-y-1 sm:grid-cols-[max-content_minmax(0,1fr)]">
            {placeholders.map(([token, key]) => (
              <Fragment key={token}>
                <dt>
                  <code className="font-mono text-xs">{token}</code>
                </dt>
                <dd className="text-muted-foreground">
                  {t(`connect.placeholders.${key}`)}
                </dd>
              </Fragment>
            ))}
          </dl>
        </div>
      ) : null}

      <ol className="grid gap-5">
        {CLIENT_STEPS[client].map((step, index) => {
          const snippet = snippets.find((item) => item.id === step)
          return (
            <li
              key={step}
              className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-3 gap-y-2"
            >
              <StepNumber number={index + 1} />
              <p className="text-sm leading-6">
                <CodeText text={t(`${prefix}.steps.${step}`)} />
              </p>
              {snippet ? (
                <CodeBlock
                  className="col-start-2"
                  label={
                    snippet.filename ?? t(`connect.snippets.${snippet.id}`)
                  }
                  code={snippet.code}
                />
              ) : null}
            </li>
          )
        })}
      </ol>

      <div className="grid gap-2 border-t pt-4">
        <h5 className="text-sm font-medium">{t('connect.guide.goodToKnow')}</h5>
        <ul className="grid gap-2 text-sm leading-6 text-muted-foreground">
          <li className="flex gap-2">
            <MessagesSquareIcon
              aria-hidden
              className="mt-1 size-4 shrink-0 text-foreground/70"
            />
            <span>
              <CodeText text={t(`${prefix}.identity`, values)} />
            </span>
          </li>
          {CLIENT_NOTES[client].map((note) => (
            <li key={note} className="flex gap-2">
              <InfoIcon aria-hidden className="mt-1 size-4 shrink-0" />
              <span>
                <CodeText text={t(`${prefix}.notes.${note}`, values)} />
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}

/**
 * The protocols a client calls, with a picker when it is set up for one at a
 * time, and the upstreams serving them or a warning when none does.
 */
function ProtocolRequirement({
  client,
  protocol,
  upstreams,
}: Pick<ClientGuideProps, 'client' | 'upstreams'> & { protocol: Protocol }) {
  const { t } = useTranslation('contextGateway')
  const choices = protocolChoices(client)
  const protocols = activeProtocols(client, protocol)
  const serving = upstreams && servingUpstreams(client, upstreams, protocol)
  const names = serving?.map((upstream) => upstream.name) ?? []
  const listed = names
    .slice(0, MAX_LISTED_UPSTREAMS)
    .join(t('connect.guide.separator'))
  const more = names.length - MAX_LISTED_UPSTREAMS
  const protocolNames = protocols
    .map((item) => protocolLabel(t, item))
    .join(' / ')

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">
          {t('connect.guide.protocol')}
        </span>
        {choices.length ? (
          <ProtocolPicker
            client={client}
            choices={choices}
            protocol={protocol}
            upstreams={upstreams}
          />
        ) : (
          protocols.map((item) => <ProtocolBadge key={item} protocol={item} />)
        )}
        {names.length ? (
          <span className="min-w-0 text-muted-foreground">
            {more > 0
              ? t('connect.guide.upstreamsMore', { names: listed, count: more })
              : t('connect.guide.upstreams', { names: listed })}
          </span>
        ) : null}
      </div>
      {serving?.length === 0 ? (
        <Notice
          tone="warning"
          title={t('connect.guide.noUpstream.title', {
            protocols: protocolNames,
          })}
          action={
            <Button
              variant="outline"
              size="sm"
              nativeButton={false}
              render={
                <Link
                  to="/context-gateway/upstreams/$upstreamId"
                  params={{ upstreamId: NEW_ID }}
                />
              }
            >
              <PlusIcon />
              {t('connect.guide.noUpstream.action')}
            </Button>
          }
        >
          <p>
            {t(
              choices.length
                ? 'connect.guide.noUpstream.descriptionPicked'
                : 'connect.guide.noUpstream.description',
              { protocols: protocolNames },
            )}
          </p>
        </Notice>
      ) : null}
    </div>
  )
}

/** Segmented links between a client's protocols, kept in `?protocol=`. */
function ProtocolPicker({
  client,
  choices,
  protocol,
  upstreams,
}: {
  client: ClientId
  choices: Protocol[]
  protocol: Protocol
  upstreams?: Upstream[]
}) {
  const { t } = useTranslation('contextGateway')
  return (
    <div
      role="group"
      aria-label={t('connect.guide.protocol')}
      className="inline-flex flex-wrap rounded-md border p-0.5"
    >
      {choices.map((choice) => {
        const active = choice === protocol
        const missing =
          upstreams !== undefined &&
          servingUpstreams(client, upstreams, choice).length === 0
        return (
          <Link
            key={choice}
            to="/context-gateway/connect"
            search={{ client, protocol: choice }}
            replace
            resetScroll={false}
            aria-current={active ? 'true' : undefined}
            title={
              missing ? t('connect.guide.noUpstream.protocolHint') : undefined
            }
            className={cn(
              'flex items-center gap-1.5 rounded px-2.5 py-1 text-xs transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
              active
                ? 'bg-muted font-medium text-foreground'
                : 'text-muted-foreground',
            )}
          >
            {protocolLabel(t, choice)}
            {missing ? (
              <>
                <TriangleAlertIcon
                  aria-hidden
                  className="size-3 text-amber-600 dark:text-amber-400"
                />
                <span className="sr-only">
                  {t('connect.guide.noUpstream.protocolHint')}
                </span>
              </>
            ) : null}
          </Link>
        )
      })}
    </div>
  )
}

function StepNumber({ number }: { number: number }) {
  return (
    <span
      aria-hidden="true"
      className="flex size-6 shrink-0 items-center justify-center rounded-full border border-dashed border-primary/50 text-xs font-medium text-primary"
    >
      {number}
    </span>
  )
}
