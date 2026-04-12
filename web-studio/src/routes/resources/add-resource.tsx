import { createFileRoute } from '@tanstack/react-router'

import { AddResourcePage } from './-components/add-resource-page'

export const Route = createFileRoute('/resources/add-resource')({
  component: AddResourcePage,
})
