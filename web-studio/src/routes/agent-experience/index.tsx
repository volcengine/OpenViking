import { AgentExperienceRoute } from '#/features/workbench/agent-experience/index'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/agent-experience/')({
  component: AgentExperienceRoute,
})
