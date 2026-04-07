import { createFileRoute } from '@tanstack/react-router'

import { AccessLegacyPage } from '#/components/legacy/access/page'

export const Route = createFileRoute('/legacy/access')({
  component: AccessLegacyPage,
})
