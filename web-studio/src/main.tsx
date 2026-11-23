import { registerServiceWorker } from '#/app/lib/service-worker'
import { queryClient } from '#/app/providers/query-client'
import {
  initializeCredentialStorage,
  credentialStorageMode,
} from '#/features/connection/lib/credential-storage'
import '#/i18n'
import i18n from '#/i18n/core'
import { routeTree } from '#/routeTree.gen'
import { getRouterBasePath } from '#/shared/lib/public-path'
import { TooltipProvider } from '#/shared/ui/tooltip'
import { QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider, createRouter } from '@tanstack/react-router'
import { ThemeProvider } from 'next-themes'
import ReactDOM from 'react-dom/client'
import { toast } from 'sonner'

// PWA: register the service worker at the SPA's base path so the scope
// matches the manifest's start_url / scope. Production builds only — the
// vite dev server's HMR doesn't play nicely with a SW intercepting requests.
if ('serviceWorker' in navigator && import.meta.env.PROD) {
  const basePath = getRouterBasePath() || '/'
  window.addEventListener('load', () => {
    void registerServiceWorker(navigator.serviceWorker, basePath)
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

async function start() {
  try {
    await initializeCredentialStorage()
    if (credentialStorageMode() === 'session') {
      window.sessionStorage.removeItem('ov_studio_workspace_selected')
    }
  } catch {
    rootElement.textContent = i18n.t('studio:credentialStorage.startError')
    return
  }
  window.addEventListener('ov-credential-storage-error', () => {
    toast.error(i18n.t('studio:credentialStorage.saveError'), {
      id: 'credential-storage',
      duration: Infinity,
    })
  })
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
}
void start()
