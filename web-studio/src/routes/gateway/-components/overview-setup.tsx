import type * as React from 'react'
import { Link } from '@tanstack/react-router'
import { ArrowRightIcon, CheckIcon, ListChecksIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import { cn } from '#/lib/utils'

import { NEW_ID } from '../-lib/search'
import { useKeys, useProfiles, useUpstreams } from '../-lib/use-gateway'
import { RecommendedProfileButton } from './recommended-profile-button'

type StepState = 'done' | 'current' | 'todo'

function StepMarker({ number, state }: { number: number; state: StepState }) {
  const { t } = useTranslation('contextGateway')
  if (state === 'done') {
    return (
      <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
        <CheckIcon className="size-3.5" />
        <span className="sr-only">{t('overview.setup.done')}</span>
      </span>
    )
  }
  return (
    <span
      className={cn(
        'flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-medium tabular-nums',
        state === 'current'
          ? 'bg-primary text-primary-foreground'
          : 'border border-dashed border-muted-foreground/40 text-muted-foreground',
      )}
    >
      {number}
    </span>
  )
}

type SetupStepProps = {
  number: number
  state: StepState
  title: string
  description: React.ReactNode
  children: React.ReactNode
}

function SetupStep({
  number,
  state,
  title,
  description,
  children,
}: SetupStepProps) {
  return (
    <li
      className={cn(
        'flex min-w-0 flex-col gap-3 rounded-lg border bg-background/70 p-4',
        state === 'current' && 'border-primary/40 shadow-xs',
      )}
    >
      <div className="flex items-center gap-2.5">
        <StepMarker number={number} state={state} />
        <p
          className={cn(
            'font-medium',
            state === 'done' && 'text-muted-foreground',
          )}
        >
          {title}
        </p>
      </div>
      <div className="text-sm leading-6 text-muted-foreground">
        {description}
      </div>
      <div className="mt-auto flex flex-wrap gap-2">{children}</div>
    </li>
  )
}

/**
 * Checklist from an empty gateway to the first request: add an upstream,
 * create a context profile (one click with recommended settings), issue a
 * key, connect a client. A step is done once its list is non-empty.
 */
export function OverviewSetup() {
  const { t } = useTranslation('contextGateway')
  const upstreams = useUpstreams()
  const profiles = useProfiles()
  const keys = useKeys()

  const counts = [upstreams.data, profiles.data, keys.data].map(
    (list) => list?.length,
  )
  const loaded = counts.every((count) => count !== undefined)
  const done = [...counts.map((count) => Boolean(count)), false]
  const current = loaded ? done.indexOf(false) : -1
  const stateOf = (index: number): StepState =>
    done[index] ? 'done' : index === current ? 'current' : 'todo'
  const variantOf = (index: number) =>
    index === current ? 'default' : 'outline'
  const [upstreamCount = 0, profileCount = 0, keyCount = 0] = counts

  const viewButton = (
    to:
      | '/context-gateway/upstreams'
      | '/context-gateway/profiles'
      | '/context-gateway/keys',
  ) => (
    <Button
      size="sm"
      variant="ghost"
      nativeButton={false}
      render={<Link to={to} />}
    >
      {t('overview.setup.view')}
      <ArrowRightIcon />
    </Button>
  )

  return (
    <Card className="border-primary/25 bg-primary/[0.025] ring-primary/10">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <span className="flex size-7 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <ListChecksIcon className="size-4" />
          </span>
          {t('overview.setup.title')}
        </CardTitle>
        <CardDescription>{t('overview.setup.description')}</CardDescription>
        {loaded ? (
          <CardAction>
            <span className="rounded-full border bg-background/70 px-2.5 py-1 text-xs text-muted-foreground tabular-nums">
              {t('overview.setup.progress', {
                done: done.filter(Boolean).length,
                total: done.length,
              })}
            </span>
          </CardAction>
        ) : null}
      </CardHeader>
      <CardContent>
        <ol className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <SetupStep
            number={1}
            state={stateOf(0)}
            title={t('overview.setup.upstream.title')}
            description={
              done[0]
                ? t('overview.setup.upstream.done', { count: upstreamCount })
                : t('overview.setup.upstream.description')
            }
          >
            {done[0] ? (
              viewButton('/context-gateway/upstreams')
            ) : (
              <Button
                size="sm"
                variant={variantOf(0)}
                nativeButton={false}
                render={
                  <Link
                    to="/context-gateway/upstreams/$upstreamId"
                    params={{ upstreamId: NEW_ID }}
                  />
                }
              >
                {t('overview.setup.upstream.action')}
              </Button>
            )}
          </SetupStep>
          <SetupStep
            number={2}
            state={stateOf(1)}
            title={t('overview.setup.profile.title')}
            description={
              done[1]
                ? t('overview.setup.profile.done', { count: profileCount })
                : t('overview.setup.profile.description')
            }
          >
            {done[1] ? (
              viewButton('/context-gateway/profiles')
            ) : (
              <>
                <RecommendedProfileButton
                  variant={variantOf(1)}
                  disabled={!profiles.data}
                />
                <Button
                  size="sm"
                  variant="ghost"
                  nativeButton={false}
                  render={
                    <Link
                      to="/context-gateway/profiles/$profileId"
                      params={{ profileId: NEW_ID }}
                    />
                  }
                >
                  {t('profiles.actions.customize')}
                </Button>
              </>
            )}
          </SetupStep>
          <SetupStep
            number={3}
            state={stateOf(2)}
            title={t('overview.setup.key.title')}
            description={
              done[2] ? (
                t('overview.setup.key.done', { count: keyCount })
              ) : (
                <>
                  <p>{t('overview.setup.key.description')}</p>
                  {loaded && !(done[0] && done[1]) ? (
                    <p className="mt-1 text-xs">
                      {t('overview.setup.key.blocked')}
                    </p>
                  ) : null}
                </>
              )
            }
          >
            {done[2] ? (
              viewButton('/context-gateway/keys')
            ) : (
              <Button
                size="sm"
                variant={variantOf(2)}
                nativeButton={false}
                render={<Link to="/context-gateway/keys" />}
              >
                {t('overview.setup.key.action')}
              </Button>
            )}
          </SetupStep>
          <SetupStep
            number={4}
            state={stateOf(3)}
            title={t('overview.setup.connect.title')}
            description={t('overview.setup.connect.description')}
          >
            <Button
              size="sm"
              variant={variantOf(3)}
              nativeButton={false}
              render={<Link to="/context-gateway/connect" />}
            >
              {t('overview.setup.connect.action')}
            </Button>
          </SetupStep>
        </ol>
      </CardContent>
    </Card>
  )
}
