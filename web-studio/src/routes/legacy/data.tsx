import { createFileRoute } from '@tanstack/react-router'

import { DataLegacyPage } from '#/components/legacy/data/page'

export const Route = createFileRoute('/legacy/data')({
  component: DataLegacyPage,
})
