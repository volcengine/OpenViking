import { IndexRoute } from '#/app/router/studio-entry'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/')({ component: IndexRoute })
