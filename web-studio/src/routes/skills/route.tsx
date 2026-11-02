import { SkillsRoute } from '#/features/workbench/skills/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/skills')({
  component: SkillsRoute,
})
