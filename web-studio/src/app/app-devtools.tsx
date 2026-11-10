import { TanStackDevtools } from '@tanstack/react-devtools'
import { TanStackRouterDevtoolsPanel } from '@tanstack/react-router-devtools'

export function AppDevtools() {
  return (
    <TanStackDevtools
      config={{
        position: 'bottom-right',
      }}
      plugins={[
        {
          name: 'TanStack Router',
          render: <TanStackRouterDevtoolsPanel />,
        },
      ]}
    />
  )
}
