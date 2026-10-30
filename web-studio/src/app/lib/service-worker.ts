export interface ServiceWorkerPaths {
  scope: string
  scriptUrl: string
}

export function resolveServiceWorkerPaths(
  basePath: string,
): ServiceWorkerPaths {
  const normalizedBasePath = basePath.trim() || '/'
  const scope =
    normalizedBasePath === '/'
      ? '/'
      : `${normalizedBasePath.replace(/\/+$/, '')}/`

  return {
    scope,
    scriptUrl: `${scope}service-worker.js`,
  }
}

export async function registerServiceWorker(
  serviceWorker: ServiceWorkerContainer,
  basePath: string,
): Promise<ServiceWorkerRegistration | undefined> {
  const { scope, scriptUrl } = resolveServiceWorkerPaths(basePath)

  try {
    return await serviceWorker.register(scriptUrl, { scope })
  } catch (error) {
    console.warn('OpenViking Studio service worker registration failed', error)
    return undefined
  }
}
