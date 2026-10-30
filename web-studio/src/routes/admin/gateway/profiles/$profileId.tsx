import { createFileRoute } from '@tanstack/react-router'

import { ProfileEditorPage } from '#/features/admin/gateway/components/profiles-editor'
import { parseProfileEditorSearch } from '#/features/admin/gateway/lib/search'

export const Route = createFileRoute('/admin/gateway/profiles/$profileId')({
  validateSearch: parseProfileEditorSearch,
  component: ProfileEditorPage,
})
