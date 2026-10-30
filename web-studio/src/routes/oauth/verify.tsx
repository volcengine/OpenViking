import { createFileRoute } from '@tanstack/react-router'
import { VerifyPage } from './-verify-page'

export const Route = createFileRoute('/oauth/verify')({ component: VerifyPage })
