import { createFileRoute } from '@tanstack/react-router'

import { ProfilesPage } from '../-components/profiles-page'

export const Route = createFileRoute('/gateway/profiles/')({
  component: ProfilesPage,
})
