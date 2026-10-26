import { createFileRoute } from '@tanstack/react-router'

// AppShell renders the connection wizard outside the connected workspace.
export const Route = createFileRoute('/config')({
  component: () => null,
})
