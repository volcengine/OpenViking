import { createFileRoute } from '@tanstack/react-router'

import { OpsLegacyPage } from '#/components/legacy/ops/page'

export const Route = createFileRoute('/legacy/ops')({
  component: OpsLegacyPage,
})
