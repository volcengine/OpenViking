import { Outlet, createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/agent-experience')({
  component: AgentExperienceLayout,
})

/** Layout shell shared by the list and detail views. */
function AgentExperienceLayout() {
  return <Outlet />
}
