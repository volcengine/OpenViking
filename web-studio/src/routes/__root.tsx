import { RootComponent, RootErrorComponent } from '#/app/router/root-layout'
import { createRootRoute } from '@tanstack/react-router'

export const Route = createRootRoute({
  component: RootComponent,
  errorComponent: RootErrorComponent,
})
