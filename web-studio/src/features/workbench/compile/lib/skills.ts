import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import type { CompileSkill } from '#/features/workbench/compile/lib/api'
import {
  BUILTIN_SKILLS_ROOT,
  fetchCompileSkills,
} from '#/features/workbench/compile/lib/api'
import { useQuery } from '@tanstack/react-query'
import type { TFunction } from 'i18next'

export function skillScope(uri: string) {
  if (uri.startsWith(`${BUILTIN_SKILLS_ROOT}/`)) return 'builtinSkills'
  if (uri.startsWith('viking://user/')) return 'mySkills'
  if (uri.startsWith('viking://agent/skills/')) return 'sharedSkills'
  return 'otherSkills'
}

// These are localized explanations, not an inventory. Only server-listed skills appear.
export function skillGuide(skill: CompileSkill, t: TFunction<'compile'>) {
  if (skillScope(skill.uri) !== 'builtinSkills') return null
  const key = skill.uri.replace(/\/$/, '').split('/').pop()
  if (
    ![
      'llm-wiki',
      'knowledge-distillation',
      'daily-report',
      'knowledge-graph',
      'ov-session-report',
    ].includes(key ?? '')
  )
    return null
  return {
    name: t(`builtin.${key}.name`),
    description: t(`builtin.${key}.description`),
    inputs: t(`builtin.${key}.inputs`),
    outputs: t(`builtin.${key}.outputs`),
    limits: t(`builtin.${key}.limits`),
    specialized: key === 'ov-session-report',
  }
}

export function useCompileSkills() {
  const { identityScopeKey, connection } = useAppConnection()
  const mine = useQuery({
    queryKey: ['compile-skills', identityScopeKey, 'mine'],
    queryFn: ({ signal }) =>
      fetchCompileSkills(
        signal,
        `viking://user/${connection.userId || 'default'}/skills`,
      ),
    staleTime: 30_000,
  })
  const shared = useQuery({
    queryKey: ['compile-skills', identityScopeKey, 'shared'],
    queryFn: ({ signal }) =>
      fetchCompileSkills(signal, 'viking://agent/skills'),
    staleTime: 30_000,
  })
  const builtin = useQuery({
    queryKey: ['compile-skills', identityScopeKey, 'builtin'],
    queryFn: ({ signal }) => fetchCompileSkills(signal, BUILTIN_SKILLS_ROOT),
    staleTime: 30_000,
  })
  return {
    mine,
    shared,
    builtin,
    data: [
      ...(builtin.data ?? []),
      ...(mine.data ?? []),
      ...(shared.data ?? []),
    ],
  }
}
