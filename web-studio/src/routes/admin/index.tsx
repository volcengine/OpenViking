import { AdminIndex } from '#/app/router/admin-entry'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/')({ component: AdminIndex })
