import { createFileRoute } from '@tanstack/react-router'

import { ProfilesPage } from '../-components/profiles-page'

export const Route = createFileRoute('/context-gateway/profiles/')({
  component: ProfilesPage,
})
