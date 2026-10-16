import { createFileRoute } from '@tanstack/react-router'

import { KeysPage } from './-components/keys-page'

export const Route = createFileRoute('/gateway/keys')({
  component: KeysPage,
})
