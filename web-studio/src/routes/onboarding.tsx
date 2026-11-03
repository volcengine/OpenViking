import { createFileRoute } from '@tanstack/react-router'

// AppShell renders first-use onboarding before the connected workspace mounts.
export const Route = createFileRoute('/onboarding')({ component: () => null })
