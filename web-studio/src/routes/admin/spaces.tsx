import { DataSpacesPage } from '#/features/admin/spaces/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/spaces')({
  component: DataSpacesPage,
})
