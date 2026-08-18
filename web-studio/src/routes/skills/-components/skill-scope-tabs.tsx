import { UserRoundIcon, UsersRoundIcon } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '#/components/ui/button'

export type SkillScope = 'agent' | 'user'

const SKILL_SCOPES: SkillScope[] = ['user', 'agent']

export const SKILL_SCOPE_ICONS = {
  agent: UsersRoundIcon,
  user: UserRoundIcon,
} satisfies Record<SkillScope, typeof UserRoundIcon>

export function getSkillsForScope<T extends { scope: SkillScope }>(
  skills: T[],
  scope: SkillScope,
): T[] {
  return skills.filter((skill) => skill.scope === scope)
}

export function SkillScopeTabs({
  activeScope,
  onScopeChange,
  skills,
}: {
  activeScope: SkillScope
  onScopeChange: (scope: SkillScope) => void
  skills: { scope: SkillScope }[]
}) {
  const { t } = useTranslation('skillsPage')

  return (
    <div
      aria-label={t('tabs.label')}
      className="inline-flex w-fit gap-1 rounded-lg border bg-muted/30 p-1"
      role="tablist"
    >
      {SKILL_SCOPES.map((scope) => {
        const ScopeIcon = SKILL_SCOPE_ICONS[scope]

        return (
          <Button
            key={scope}
            id={`skills-${scope}-tab`}
            type="button"
            role="tab"
            size="sm"
            variant={activeScope === scope ? 'secondary' : 'ghost'}
            aria-controls={`skills-${scope}-panel`}
            aria-selected={activeScope === scope}
            tabIndex={activeScope === scope ? 0 : -1}
            className="min-w-28 gap-1.5"
            onClick={() => onScopeChange(scope)}
          >
            <ScopeIcon />
            {t(`tabs.${scope}`)}
            <span className="text-xs text-muted-foreground">
              {getSkillsForScope(skills, scope).length}
            </span>
          </Button>
        )
      })}
    </div>
  )
}
