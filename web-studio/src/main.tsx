import '#/i18n'
import ReactDOM from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createRouter } from '@tanstack/react-router'
import { ThemeProvider } from 'next-themes'
import { routeTree } from './routeTree.gen'
import { TooltipProvider } from './components/ui/tooltip'
import { queryClient } from './lib/query-client'
import { getRouterBasePath } from './lib/public-path'

const router = createRouter({
  routeTree,
  basepath: getRouterBasePath(),
  defaultPreload: 'intent',
  scrollRestoration: true,
})

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router
  }
}

const rootElement = document.getElementById('app')!

if (!rootElement.innerHTML) {
  const root = ReactDOM.createRoot(rootElement)
  root.render(
    <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <RouterProvider router={router} />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>,
  )
}
