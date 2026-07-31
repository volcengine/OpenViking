import * as React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  ChevronRightIcon,
  FileCode2Icon,
  LoaderCircleIcon,
  RefreshCwIcon,
  SparklesIcon,
  UserRoundIcon,
  UsersRoundIcon,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Badge } from '#/components/ui/badge'
import { Button } from '#/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '#/components/ui/card'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '#/components/ui/sheet'
import { useAppConnection } from '#/hooks/use-app-connection'
import { getOvResult, isOvClientError, ovClient } from '#/lib/ov-client'

export const Route = createFileRoute('/skills')({
  component: SkillsRoute,
})

type SkillScope = 'agent' | 'user'

type SkillItem = {
  description: string
  name: string
  scope: SkillScope
  uri: string
}

type SkillListResult = {
  skills?: unknown[]
}

type SkillFile = {
  isDir: boolean
  name: string
  path: string
}

type SkillDetail = SkillItem & {
  allowedTools: string[]
  content: string
  files: SkillFile[]
  overview: string
  tags: string[]
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : null
}

function normalizeSkills(value: unknown): SkillItem[] {
  const result = asRecord(value)
  const skills = Array.isArray(result?.skills) ? result.skills : []

  return skills.flatMap((rawSkill) => {
    const skill = asRecord(rawSkill)
    const name = typeof skill?.name === 'string' ? skill.name : ''
    const uri =
      typeof skill?.uri === 'string'
        ? skill.uri
        : typeof skill?.root_uri === 'string'
          ? skill.root_uri
          : ''

    if (!name || !uri) {
      return []
    }

    return [
      {
        description:
          typeof skill?.description === 'string' ? skill.description : '',
        name,
        scope: uri.startsWith('viking://agent/') ? 'agent' : 'user',
        uri,
      } satisfies SkillItem,
    ]
  })
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === 'string')
    : []
}

function normalizeSkillDetail(value: unknown, fallback: SkillItem): SkillDetail {
  const detail = asRecord(value)
  const files = Array.isArray(detail?.files) ? detail.files : []

  return {
    allowedTools: stringArray(detail?.allowed_tools),
    content: typeof detail?.content === 'string' ? detail.content : '',
    description:
      typeof detail?.description === 'string'
        ? detail.description
        : fallback.description,
    files: files.flatMap((rawFile) => {
      const file = asRecord(rawFile)
      const name = typeof file?.name === 'string' ? file.name : ''
      if (!name) return []
      return [
        {
          isDir: Boolean(file?.is_dir),
          name,
          path: typeof file?.path === 'string' ? file.path : name,
        },
      ]
    }),
    name: typeof detail?.name === 'string' ? detail.name : fallback.name,
    overview: typeof detail?.overview === 'string' ? detail.overview : '',
    scope: fallback.scope,
    tags: stringArray(detail?.tags),
    uri: typeof detail?.uri === 'string' ? detail.uri : fallback.uri,
  }
}

function getErrorMessage(error: unknown): string {
  if (isOvClientError(error) || error instanceof Error) {
    return error.message
  }
  const record = asRecord(error)
  if (typeof record?.message === 'string') {
    return record.message
  }
  return JSON.stringify(error) || String(error)
}

async function fetchSkills(): Promise<SkillItem[]> {
  const result = await getOvResult<SkillListResult>(
    ovClient.client.get({
      query: {
        node_limit: 1000,
      },
      url: '/api/v1/skills',
    }),
  )
  return normalizeSkills(result)
}

async function fetchSkillDetail(skill: SkillItem): Promise<SkillDetail> {
  const targetUri = skill.uri.slice(0, skill.uri.lastIndexOf('/'))
  const result = await getOvResult<unknown>(
    ovClient.client.get({
      query: {
        include_content: true,
        include_files: true,
        target_uri: targetUri,
      },
      url: `/api/v1/skills/${encodeURIComponent(skill.name)}`,
    }),
  )
  return normalizeSkillDetail(result, skill)
}

function SkillsRoute() {
  const { t } = useTranslation('skillsPage')
  const { identityScopeKey } = useAppConnection()
  const [selectedSkill, setSelectedSkill] = React.useState<SkillItem | null>(
    null,
  )
  const skillsQuery = useQuery({
    queryFn: fetchSkills,
    queryKey: ['skills', identityScopeKey],
    staleTime: 30_000,
  })
  const skills = skillsQuery.data ?? []
  const connectionUnavailable =
    isOvClientError(skillsQuery.error) &&
    skillsQuery.error.code === 'NETWORK_ERROR'
  const detailQuery = useQuery({
    enabled: Boolean(selectedSkill),
    queryFn: () => fetchSkillDetail(selectedSkill as SkillItem),
    queryKey: ['skill-detail', identityScopeKey, selectedSkill?.uri],
  })

  return (
    <div className="flex w-full min-w-0 flex-col gap-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="grid gap-1.5">
          <h1 className="text-2xl font-semibold tracking-tight">
            {t('title')}
          </h1>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            {t('description')}
          </p>
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={skillsQuery.isFetching}
          onClick={() => void skillsQuery.refetch()}
        >
          <RefreshCwIcon
            className={skillsQuery.isFetching ? 'animate-spin' : undefined}
          />
          {t('refresh')}
        </Button>
      </header>

      {skillsQuery.isLoading ? (
        <Card className="min-h-56 items-center justify-center">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <LoaderCircleIcon className="size-4 animate-spin" />
            {t('loading')}
          </div>
        </Card>
      ) : skillsQuery.isError ? (
        <Card className="min-h-56 items-center justify-center px-6 text-center">
          <SparklesIcon className="size-8 text-destructive/70" />
          <div className="grid gap-1">
            <p className="font-medium">{t('loadFailed')}</p>
            <p className="max-w-xl text-sm text-muted-foreground">
              {connectionUnavailable
                ? t('networkError')
                : getErrorMessage(skillsQuery.error)}
            </p>
            {connectionUnavailable ? (
              <Button
                render={<Link to="/settings" />}
                nativeButton={false}
                variant="outline"
                size="sm"
                className="mx-auto mt-2"
              >
                {t('connectionSettings')}
              </Button>
            ) : null}
          </div>
        </Card>
      ) : skills.length === 0 ? (
        <Card className="min-h-56 items-center justify-center px-6 text-center">
          <div className="flex size-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <SparklesIcon className="size-5" />
          </div>
          <div className="grid max-w-md gap-1">
            <p className="font-medium">{t('empty')}</p>
            <p className="text-sm text-muted-foreground">
              {t('emptyDescription')}
            </p>
          </div>
        </Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {skills.map((skill) => {
            const ScopeIcon =
              skill.scope === 'user' ? UserRoundIcon : UsersRoundIcon

            return (
              <button
                key={`${skill.scope}:${skill.uri}`}
                type="button"
                className="min-w-0 rounded-xl text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                aria-label={t('viewDetail', { name: skill.name })}
                onClick={() => setSelectedSkill(skill)}
              >
                <Card
                  size="sm"
                  className="h-full transition-colors hover:bg-muted/35"
                >
                  <CardHeader>
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 items-center gap-2.5">
                        <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                          <SparklesIcon className="size-4" />
                        </div>
                        <CardTitle className="truncate">{skill.name}</CardTitle>
                      </div>
                      <Badge variant="outline" className="gap-1 font-normal">
                        <ScopeIcon />
                        {t(`scopes.${skill.scope}`)}
                      </Badge>
                    </div>
                    {skill.description ? (
                      <CardDescription className="line-clamp-2 pt-1 leading-5">
                        {skill.description}
                      </CardDescription>
                    ) : null}
                  </CardHeader>
                  <CardContent className="mt-auto">
                    <div className="flex items-center justify-between gap-3">
                      <code className="min-w-0 truncate text-xs text-muted-foreground">
                        {skill.uri}
                      </code>
                      <span className="flex shrink-0 items-center gap-0.5 text-xs font-medium text-primary">
                        {t('detail')}
                        <ChevronRightIcon className="size-3.5" />
                      </span>
                    </div>
                  </CardContent>
                </Card>
              </button>
            )
          })}
        </div>
      )}

      <Sheet
        open={Boolean(selectedSkill)}
        onOpenChange={(open) => {
          if (!open) setSelectedSkill(null)
        }}
      >
        <SheetContent className="gap-0 sm:max-w-2xl">
          <SheetHeader className="border-b px-6 py-5">
            <div className="flex items-center gap-2 pr-10">
              <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <SparklesIcon className="size-4" />
              </div>
              <SheetTitle className="truncate text-lg">
                {selectedSkill?.name}
              </SheetTitle>
            </div>
            <SheetDescription className="truncate font-mono text-xs">
              {selectedSkill?.uri}
            </SheetDescription>
          </SheetHeader>

          <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
            {detailQuery.isLoading ? (
              <div className="flex min-h-48 items-center justify-center gap-2 text-muted-foreground">
                <LoaderCircleIcon className="size-4 animate-spin" />
                {t('detailLoading')}
              </div>
            ) : detailQuery.isError ? (
              <div className="flex min-h-48 flex-col items-center justify-center gap-1 text-center">
                <p className="font-medium">{t('detailLoadFailed')}</p>
                <p className="max-w-md text-sm text-muted-foreground">
                  {getErrorMessage(detailQuery.error)}
                </p>
              </div>
            ) : detailQuery.data ? (
              <div className="grid gap-6">
                <div className="grid grid-cols-2 gap-2">
                  <DetailMetric
                    icon={<UserRoundIcon />}
                    label={t('metrics.scope')}
                    value={t(`scopes.${detailQuery.data.scope}`)}
                  />
                  <DetailMetric
                    icon={<FileCode2Icon />}
                    label={t('metrics.files')}
                    value={String(detailQuery.data.files.length)}
                  />
                </div>

                {detailQuery.data.description ? (
                  <DetailSection title={t('sections.description')}>
                    <p className="leading-6 text-muted-foreground">
                      {detailQuery.data.description}
                    </p>
                  </DetailSection>
                ) : null}

                {detailQuery.data.overview ? (
                  <DetailSection title={t('sections.overview')}>
                    <pre className="whitespace-pre-wrap font-sans text-sm leading-6 text-muted-foreground">
                      {detailQuery.data.overview}
                    </pre>
                  </DetailSection>
                ) : null}

                {detailQuery.data.tags.length > 0 ||
                detailQuery.data.allowedTools.length > 0 ? (
                  <div className="grid gap-4 sm:grid-cols-2">
                    <DetailTagList
                      title={t('sections.tags')}
                      values={detailQuery.data.tags}
                      empty={t('none')}
                    />
                    <DetailTagList
                      title={t('sections.allowedTools')}
                      values={detailQuery.data.allowedTools}
                      empty={t('none')}
                    />
                  </div>
                ) : null}

                <DetailSection title={t('sections.files')}>
                  {detailQuery.data.files.length > 0 ? (
                    <div className="overflow-hidden rounded-lg border">
                      {detailQuery.data.files.map((file) => (
                        <div
                          key={file.path}
                          className="flex items-center gap-2 border-b px-3 py-2 last:border-b-0"
                        >
                          <FileCode2Icon className="size-4 shrink-0 text-muted-foreground" />
                          <span className="min-w-0 flex-1 truncate">
                            {file.path}
                          </span>
                          {file.isDir ? (
                            <Badge variant="outline">{t('directory')}</Badge>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">{t('none')}</p>
                  )}
                </DetailSection>

                {detailQuery.data.content ? (
                  <DetailSection title={t('sections.content')}>
                    <pre className="overflow-x-auto whitespace-pre-wrap rounded-lg border bg-muted/30 p-4 font-mono text-xs leading-5">
                      {detailQuery.data.content}
                    </pre>
                  </DetailSection>
                ) : null}
              </div>
            ) : null}
          </div>
        </SheetContent>
      </Sheet>
    </div>
  )
}

function DetailMetric({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode
  label: string
  value: string
}) {
  return (
    <div className="min-w-0 rounded-lg border bg-muted/20 p-3">
      <div className="flex items-center gap-1.5 text-xs text-muted-foreground [&_svg]:size-3.5">
        {icon}
        {label}
      </div>
      <p className="mt-1 truncate font-medium">{value}</p>
    </div>
  )
}

function DetailSection({
  children,
  title,
}: {
  children: React.ReactNode
  title: string
}) {
  return (
    <section className="grid gap-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  )
}

function DetailTagList({
  empty,
  title,
  values,
}: {
  empty: string
  title: string
  values: string[]
}) {
  return (
    <DetailSection title={title}>
      {values.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {values.map((value) => (
            <Badge key={value} variant="secondary" className="font-normal">
              {value}
            </Badge>
          ))}
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">{empty}</p>
      )}
    </DetailSection>
  )
}
