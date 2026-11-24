import { VerifyPage } from '#/features/oauth/verify-page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/oauth/verify')({
  component: VerifyPage,
})
