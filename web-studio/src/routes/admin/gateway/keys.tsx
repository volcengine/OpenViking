import { createFileRoute } from '@tanstack/react-router'

import { KeysPage } from '#/features/admin/gateway/components/keys-page'

export const Route = createFileRoute('/admin/gateway/keys')({
  component: KeysPage,
})
