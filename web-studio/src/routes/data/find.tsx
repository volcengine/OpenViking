import { createFileRoute } from '@tanstack/react-router'

import { FindPage } from '#/components/data/find-page'

export const Route = createFileRoute('/data/find')({
  component: FindPage,
})
