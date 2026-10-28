import { Outlet, createFileRoute } from '@tanstack/react-router'

export const EXPERIENCE_PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const

type ExperienceSearch = { page?: number; pageSize?: number; q?: string }

export const Route = createFileRoute('/agent-experience')({
  validateSearch: (search: Record<string, unknown>): ExperienceSearch => ({
    page:
      typeof search.page === 'number' &&
      Number.isSafeInteger(search.page) &&
      search.page > 0
        ? search.page
        : undefined,
    pageSize: EXPERIENCE_PAGE_SIZE_OPTIONS.includes(
      search.pageSize as (typeof EXPERIENCE_PAGE_SIZE_OPTIONS)[number],
    )
      ? (search.pageSize as number)
      : undefined,
    q: typeof search.q === 'string' ? search.q.trim() : undefined,
  }),
  component: AgentExperienceLayout,
})

/** Layout shell shared by the list and detail views. */
function AgentExperienceLayout() {
  return <Outlet />
}
