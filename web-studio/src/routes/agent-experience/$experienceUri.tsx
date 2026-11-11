import { ExperienceDetailRoute } from '#/features/workbench/agent-experience/$experienceUri'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/agent-experience/$experienceUri')({
  component: ExperienceDetailRoute,
})
