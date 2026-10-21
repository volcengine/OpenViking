import { createFileRoute } from '@tanstack/react-router'

import { ProfileEditorPage } from '../-components/profiles-editor'
import { parseProfileEditorSearch } from '../-lib/search'

export const Route = createFileRoute('/gateway/profiles/$profileId')({
  validateSearch: parseProfileEditorSearch,
  component: ProfileEditorPage,
})
