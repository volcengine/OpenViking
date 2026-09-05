import {
  ChevronRightIcon,
  LoaderCircleIcon,
  Share2Icon,
  SparklesIcon,
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

import { SKILL_SCOPE_ICONS } from './skill-scope-tabs'
import type { SkillScope } from './skill-scope-tabs'

export type SkillCardItem = {
  description: string
  name: string
  scope: SkillScope
  uri: string
}

export function SkillCard({
  isSharing,
  onOpen,
  onShare,
  skill,
}: {
  isSharing: boolean
  onOpen: () => void
  onShare: () => void
  skill: SkillCardItem
}) {
  const { t } = useTranslation('skillsPage')
  const ScopeIcon = SKILL_SCOPE_ICONS[skill.scope]

  return (
    <Card
      size="sm"
      className="relative h-full transition-colors hover:bg-muted/35"
    >
      <button
        type="button"
        className="absolute inset-0 z-0 rounded-xl outline-none focus-visible:ring-3 focus-visible:ring-inset focus-visible:ring-ring/50"
        aria-label={t('viewDetail', { name: skill.name })}
        onClick={onOpen}
      />
      <CardHeader className="pointer-events-none relative z-10">
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
      <CardContent className="pointer-events-none relative z-10 mt-auto">
        <div className="flex items-center justify-between gap-3">
          <code className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            {skill.uri}
          </code>
          <div className="flex shrink-0 items-center gap-1">
            {skill.scope === 'user' ? (
              <Button
                type="button"
                variant="outline"
                size="xs"
                className="pointer-events-auto"
                disabled={isSharing}
                aria-label={t('shareSkill', { name: skill.name })}
                onClick={onShare}
              >
                {isSharing ? (
                  <LoaderCircleIcon className="animate-spin" />
                ) : (
                  <Share2Icon />
                )}
                {isSharing ? t('sharing') : t('share')}
              </Button>
            ) : null}
            <span className="flex h-6 items-center gap-1 px-2 text-xs font-medium text-primary">
              {t('detail')}
              <ChevronRightIcon />
            </span>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
