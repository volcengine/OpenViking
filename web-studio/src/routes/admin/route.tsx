import { AdminRoute } from '#/app/router/admin-boundary'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin')({ component: AdminRoute })
