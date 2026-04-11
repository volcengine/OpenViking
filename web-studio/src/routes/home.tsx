import { createFileRoute } from '@tanstack/react-router'

import { HomePage } from '#/components/home-page'

export const Route = createFileRoute('/home')({
  component: HomePage,
})
