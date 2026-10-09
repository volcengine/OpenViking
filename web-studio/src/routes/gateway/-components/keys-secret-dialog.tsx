import { Link } from '@tanstack/react-router'
import { CheckIcon, ExternalLinkIcon, TriangleAlertIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '#/components/ui/dialog'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '#/components/ui/tabs'

import type { IssuedKey, Upstream } from '../-lib/api'
import {
  clientSnippets,
  defaultProtocol,
  servingUpstreams,
} from '../-lib/client-guides'
import type { ClientId } from '../-lib/client-guides'
import { protocolLabel } from '../-lib/localize'
import { servedModels } from '../-lib/upstream-schema'
import { CodeBlock } from './code-block'
import { CopyButton } from './copy-button'
import { Notice } from './notice'

/** Clients offered right after issuing; the Connect tab covers the rest. */
const SECRET_CLIENTS = [
  'claude-code',
  'codex',
  'chat',
] as const satisfies readonly ClientId[]

/** A model to fill into snippets: an allowed model this client can reach, else any it can. */
export function snippetModel(
  allowed: string[],
  upstreams: Upstream[],
): string | undefined {
  const served = upstreams.flatMap(servedModels)
  if (allowed.length) {
    return allowed.find((model) => served.includes(model)) ?? allowed[0]
  }
  return served[0]
}

type KeysSecretDialogProps = {
  open: boolean
  /** Kept while the dialog closes so its content doesn't blank out. */
  issued: IssuedKey | null
  /** Gateway address clients use. */
  baseUrl: string
  /** Name of the key's context profile, when known. */
  profileName?: string
  /** All upstreams; the key's own are picked by id. */
  upstreams: Upstream[]
  /** Called by the confirm button, the only way to close the dialog. */
  onDone: () => void
  /** Called once the dialog has finished closing, to drop the secret. */
  onClosed?: () => void
}

/** Ignores Escape and outside clicks: the secret can't be shown again. */
function keepOpen() {
  // Only the explicit confirm button closes this dialog.
}

/**
 * Shows a newly issued gateway key once, with setup snippets that already
 * contain the key and the gateway address.
 */
export function KeysSecretDialog({
  open,
  issued,
  baseUrl,
  profileName,
  upstreams,
  onDone,
  onClosed,
}: KeysSecretDialogProps) {
  const { t } = useTranslation('contextGateway')
  const keyUpstreams = issued
    ? upstreams.filter((upstream) => issued.upstream_ids.includes(upstream.id))
    : []
  const firstUsable =
    SECRET_CLIENTS.find(
      (client) => servingUpstreams(client, keyUpstreams).length > 0,
    ) ?? SECRET_CLIENTS[0]

  return (
    <Dialog
      open={open}
      disablePointerDismissal
      onOpenChange={keepOpen}
      onOpenChangeComplete={(isOpen) => {
        if (!isOpen) onClosed?.()
      }}
    >
      <DialogContent showCloseButton={false} className="gap-5 sm:max-w-2xl">
        {issued ? (
          <>
            <DialogHeader>
              <DialogTitle className="text-lg">
                {t('keys.secret.title')}
              </DialogTitle>
              <DialogDescription className="flex items-start gap-2 rounded-md border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-amber-800 dark:text-amber-200">
                <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" />
                {t('keys.secret.once')}
              </DialogDescription>
            </DialogHeader>

            <div className="grid gap-3">
              <div className="flex items-start gap-2 rounded-lg border bg-muted/30 py-2 pr-2 pl-3">
                <code className="min-w-0 flex-1 py-1 font-mono text-sm break-all">
                  {issued.key}
                </code>
                <CopyButton
                  value={issued.key}
                  label={t('keys.secret.copyKey')}
                  size="icon-sm"
                />
              </div>
              <dl className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
                <div className="flex min-w-0 items-baseline gap-2">
                  <dt className="text-muted-foreground">
                    {t('keys.secret.user')}
                  </dt>
                  <dd className="truncate font-mono text-xs">
                    {issued.user_id}
                  </dd>
                </div>
                <div className="flex min-w-0 items-baseline gap-2">
                  <dt className="text-muted-foreground">
                    {t('keys.secret.profile')}
                  </dt>
                  <dd className="truncate">
                    {profileName ?? issued.policy_id}
                  </dd>
                </div>
              </dl>
            </div>

            <section className="grid min-w-0 gap-3 border-t pt-4">
              <h3 className="text-sm font-medium">
                {t('keys.secret.connectTitle')}
              </h3>
              <Tabs defaultValue={firstUsable} className="min-w-0 gap-3">
                <TabsList className="max-w-full overflow-x-auto">
                  {SECRET_CLIENTS.map((client) => (
                    <TabsTrigger key={client} value={client} className="px-3">
                      {t(`connect.clients.${client}.name`)}
                    </TabsTrigger>
                  ))}
                </TabsList>
                {SECRET_CLIENTS.map((client) => {
                  const protocol = defaultProtocol(client, keyUpstreams)
                  const reachable = servingUpstreams(
                    client,
                    keyUpstreams,
                    protocol,
                  )
                  const snippets = clientSnippets(client, {
                    baseUrl,
                    protocol,
                    key: issued.key,
                    model: snippetModel(issued.models, reachable),
                  })
                  return (
                    <TabsContent
                      key={client}
                      value={client}
                      className="grid min-w-0 gap-3"
                    >
                      <p className="text-sm leading-6 text-muted-foreground">
                        {t(`keys.secret.clients.${client}.intro`)}
                      </p>
                      {reachable.length === 0 ? (
                        <Notice tone="warning">
                          {t('keys.secret.noProtocol', {
                            protocol: protocolLabel(t, protocol),
                            client: t(`connect.clients.${client}.name`),
                          })}
                        </Notice>
                      ) : null}
                      {snippets.map((snippet) => (
                        <CodeBlock
                          key={snippet.id}
                          code={snippet.code}
                          label={
                            snippet.filename ??
                            t(`connect.snippets.${snippet.id}`)
                          }
                        />
                      ))}
                    </TabsContent>
                  )
                })}
              </Tabs>
              <div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="-ml-2 text-muted-foreground"
                  nativeButton={false}
                  render={
                    <Link
                      to="/context-gateway/connect"
                      target="_blank"
                      rel="noreferrer"
                    />
                  }
                >
                  <ExternalLinkIcon />
                  {t('keys.secret.moreClients')}
                </Button>
              </div>
            </section>

            <DialogFooter>
              <Button type="button" onClick={onDone}>
                <CheckIcon />
                {t('keys.secret.done')}
              </Button>
            </DialogFooter>
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
