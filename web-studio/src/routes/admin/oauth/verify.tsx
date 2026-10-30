import { createFileRoute } from '@tanstack/react-router'
import { VerifyPage } from '#/routes/oauth/-verify-page'

export const Route = createFileRoute('/admin/oauth/verify')({
  component: VerifyPage,
})
