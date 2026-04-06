import { createFileRoute } from '@tanstack/react-router'

import { SettingsPage } from '#/components/access/settings-page'

export const Route = createFileRoute('/access/settings')({
  component: SettingsPage,
})
