import '#/i18n'
import ReactDOM from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createRouter } from '@tanstack/react-router'
import { ThemeProvider } from 'next-themes'
import { routeTree } from './routeTree.gen'
import { TooltipProvider } from './components/ui/tooltip'
import { queryClient } from './lib/query-client'
import { getRouterBasePath } from './lib/public-path'
import { loadStudioRuntime } from './lib/studio-runtime'

// PWA: register the service worker at the SPA's base path so the scope
// matches the manifest's start_url / scope. Production builds only — the
// vite dev server's HMR doesn't play nicely with a SW intercepting requests.
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  const basePath = getRouterBasePath() || '/'
  const swUrl = `${basePath}${basePath.endsWith('/') ? '' : '/'}service-worker.js`
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register(swUrl, { scope: basePath })
  })
}

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

function renderApp() {
  if (rootElement.innerHTML) {
    return
  }
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

// Resolve the optional auto-proxy runtime config before bootstrapping so
// AppConnectionProvider can synchronously decide whether to suppress the
// connection dialog and skip persisting credentials in the browser.
void loadStudioRuntime().finally(renderApp)
