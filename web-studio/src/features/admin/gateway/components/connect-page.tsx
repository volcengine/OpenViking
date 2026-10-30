import { Link, getRouteApi } from '@tanstack/react-router'
import {
  KeyRoundIcon,
  MessagesSquareIcon,
  PuzzleIcon,
  SlidersHorizontalIcon,
} from 'lucide-react'
import type * as React from 'react'
import { useTranslation } from 'react-i18next'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { Button } from '#/shared/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/shared/ui/card'

import { CodeBlock } from '#/features/admin/gateway/components/code-block'
import { ClientGuide } from '#/features/admin/gateway/components/connect-client-guide'
import { CodeText } from '#/features/admin/gateway/components/connect-text'
import { CopyButton } from '#/features/admin/gateway/components/copy-button'
import { DocsLink } from '#/features/admin/gateway/components/docs-link'
import { LoadingState } from '#/features/admin/gateway/components/empty-state'
import { Notice } from '#/features/admin/gateway/components/notice'
import { SectionHeader } from '#/features/admin/gateway/components/section-header'
import type { ConnectionInfo, Upstream } from '#/features/admin/gateway/lib/api'
import type { DocsSection } from '#/features/admin/gateway/lib/client-guides'
import {
  CLIENT_DOCS,
  CLIENT_IDS,
  SESSION_HEADERS,
} from '#/features/admin/gateway/lib/client-guides'
import { isLoopbackUrl } from '#/features/admin/gateway/lib/format'
import { authModeLabel } from '#/features/admin/gateway/lib/localize'
import { UPSTREAM_KEY_HEADER } from '#/features/admin/gateway/lib/upstream-schema'
import {
  useConnectionInfo,
  useUpstreams,
} from '#/features/admin/gateway/lib/use-gateway'

const route = getRouteApi('/admin/gateway/connect')

/** Clients known to send a session header, shown next to it. */
const HEADER_SENDERS: Record<string, string> = {
  'x-claude-code-session-id': 'Claude Code',
  'x-opencode-session-id': 'OpenCode',
  'session-id': 'Codex CLI',
}

const UPSTREAM_KEY_EXAMPLE = `${UPSTREAM_KEY_HEADER}: <provider-api-key>`
const PLUGIN_HEADER_EXAMPLE = 'X-OpenViking-Plugin: <plugin-name>'
const CONTEXT_HEADER_EXAMPLE =
  'X-OpenViking-Context: {"recall":false,"max_tokens":800}'

/**
 * Gateway address and client setup guides (`?client=` picks the client,
 * `?protocol=` the protocol for clients set up per protocol).
 */
export function ConnectPage() {
  const { t } = useTranslation('gateway')
  const { client = CLIENT_IDS[0], protocol } = route.useSearch()
  const info = useConnectionInfo()
  const upstreams = useUpstreams()

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <SectionHeader
        description={t('connect.description')}
        actions={
          <DocsLink section={CLIENT_DOCS[client]}>
            {t('connect.fullGuide')}
          </DocsLink>
        }
      />
      {info.data ? (
        <>
          <AddressCard info={info.data} />
          <ClientGuide
            client={client}
            protocol={protocol}
            baseUrl={info.data.base_url}
            upstreams={upstreams.data}
          />
          <div className="grid gap-4 lg:grid-cols-2">
            <IdentityCard />
            <PassthroughCard upstreams={upstreams.data} />
            <ContextCard />
            <PluginCard />
          </div>
        </>
      ) : (
        <LoadingState />
      )}
    </div>
  )
}

function AddressCard({ info }: { info: ConnectionInfo }) {
  const { t } = useTranslation('gateway')
  const { isGatewayHost } = useAppConnection()
  const base = info.base_url.replace(/\/+$/, '')
  const warning = isLoopbackUrl(base)
    ? 'loopback'
    : info.public_url_configured
      ? null
      : 'notPublic'

  return (
    <Card>
      <CardHeader>
        <CardTitle role="heading" aria-level={3}>
          {t('connect.address.title')}
        </CardTitle>
        <CardDescription className="max-w-3xl leading-6">
          <CodeText text={t('connect.address.description')} />
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <dl className="grid gap-2">
          <AddressRow label={t('connect.address.anthropic')} value={base} />
          <AddressRow
            label={t('connect.address.openai')}
            value={`${base}/v1`}
          />
        </dl>
        {warning ? (
          <Notice
            tone="warning"
            title={t(`connect.address.${warning}.title`)}
            action={
              <DocsLink section="publicAddress">{t('docs.learnMore')}</DocsLink>
            }
          >
            <p>
              <CodeText
                text={t(
                  `connect.address.${warning}.${isGatewayHost ? 'standalone' : 'embedded'}`,
                )}
              />
            </p>
          </Notice>
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-md border border-border/70 bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
          <span className="flex min-w-0 items-center gap-2">
            <KeyRoundIcon aria-hidden className="size-4 shrink-0" />
            <span>
              <CodeText text={t('connect.address.keys')} />
            </span>
          </span>
          <Button
            variant="outline"
            size="sm"
            nativeButton={false}
            render={<Link to="/admin/gateway/keys" />}
          >
            {t('connect.address.manageKeys')}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function AddressRow({ label, value }: { label: string; value: string }) {
  const { t } = useTranslation('gateway')
  return (
    <div className="grid gap-1 sm:grid-cols-[minmax(0,16rem)_minmax(0,1fr)] sm:items-center sm:gap-3">
      <dt className="text-sm text-muted-foreground">{label}</dt>
      <dd className="flex min-w-0 items-center gap-1 rounded-md border bg-muted/30 py-1 pr-1 pl-3">
        <span
          className="min-w-0 flex-1 truncate font-mono text-sm"
          title={value}
        >
          {value}
        </span>
        <CopyButton value={value} label={t('address.copy')} />
      </dd>
    </div>
  )
}

function ReferenceCard({
  icon,
  title,
  description,
  docs,
  children,
}: {
  icon: React.ReactNode
  title: string
  description: React.ReactNode
  /** Docs section with the details, linked under the content. */
  docs: DocsSection
  children: React.ReactNode
}) {
  return (
    <Card size="sm">
      <CardHeader>
        <CardTitle
          role="heading"
          aria-level={3}
          className="flex items-center gap-2 [&_svg]:size-4 [&_svg]:text-muted-foreground"
        >
          {icon}
          {title}
        </CardTitle>
        <CardDescription className="leading-6">{description}</CardDescription>
      </CardHeader>
      <CardContent className="grid content-start gap-3 text-sm leading-6 text-muted-foreground">
        {children}
        <p>
          <DocsLink section={docs} variant="text" />
        </p>
      </CardContent>
    </Card>
  )
}

function IdentityCard() {
  const { t } = useTranslation('gateway')
  return (
    <ReferenceCard
      icon={<MessagesSquareIcon />}
      title={t('connect.identity.title')}
      description={t('connect.identity.description')}
      docs="conversations"
    >
      <ol className="grid gap-1">
        {SESSION_HEADERS.map((header, index) => (
          <li key={header} className="flex flex-wrap items-baseline gap-x-2">
            <span className="w-4 text-xs tabular-nums">{index + 1}</span>
            <code className="font-mono text-xs text-foreground">{header}</code>
            {HEADER_SENDERS[header] ? (
              <span className="text-xs">{HEADER_SENDERS[header]}</span>
            ) : null}
          </li>
        ))}
      </ol>
      <p>
        <CodeText text={t('connect.identity.fallback')} />
      </p>
      <p>{t('connect.identity.scope')}</p>
    </ReferenceCard>
  )
}

function PassthroughCard({ upstreams }: { upstreams?: Upstream[] }) {
  const { t } = useTranslation('gateway')
  const names = upstreams
    ?.filter((upstream) => upstream.auth_mode === 'passthrough')
    .map((upstream) => upstream.name)
  return (
    <ReferenceCard
      icon={<KeyRoundIcon />}
      title={t('connect.passthrough.title')}
      description={t('connect.passthrough.description', {
        mode: authModeLabel(t, 'passthrough'),
      })}
      docs="providerKey"
    >
      <CodeBlock code={UPSTREAM_KEY_EXAMPLE} wrap />
      <p>{t('connect.passthrough.note')}</p>
      {names ? (
        <p>
          {names.length
            ? t('connect.passthrough.upstreams', {
                names: names.join(t('connect.guide.separator')),
              })
            : t('connect.passthrough.none')}
        </p>
      ) : null}
    </ReferenceCard>
  )
}

function ContextCard() {
  const { t } = useTranslation('gateway')
  return (
    <ReferenceCard
      icon={<SlidersHorizontalIcon />}
      title={t('connect.context.title')}
      description={t('connect.context.description')}
      docs="contextHeader"
    >
      <CodeBlock code={CONTEXT_HEADER_EXAMPLE} wrap />
      <p>
        <CodeText text={t('connect.context.tools')} />
      </p>
      <p>{t('connect.context.scope')}</p>
    </ReferenceCard>
  )
}

function PluginCard() {
  const { t } = useTranslation('gateway')
  return (
    <ReferenceCard
      icon={<PuzzleIcon />}
      title={t('connect.plugin.title')}
      description={t('connect.plugin.description')}
      docs="pluginDetected"
    >
      <p>{t('connect.plugin.detection')}</p>
      <CodeBlock code={PLUGIN_HEADER_EXAMPLE} wrap />
      <p>{t('connect.plugin.mcp')}</p>
      <p>{t('connect.plugin.restart')}</p>
    </ReferenceCard>
  )
}
