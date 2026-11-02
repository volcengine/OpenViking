import type { ServiceProvider, ServiceSelection } from '../studio-contract'

export type { ServiceProvider, ServiceSelection } from '../studio-contract'

// URL detection identifies the connected service, not the Studio edition or key permissions.
// Version numbers and encoded key contents are not provider identifiers.
export function identifyServiceProvider(baseUrl: string): ServiceProvider {
  try {
    const { hostname, protocol } = new URL(baseUrl)
    if (!['http:', 'https:'].includes(protocol)) return 'unknown'
    if (/^api\.vikingdb\.[a-z0-9-]+\.volces\.com$/.test(hostname)) {
      return 'volcengine'
    }
    // All other HTTP deployments default to open source; hosted proxies can override this.
    return 'opensource'
  } catch {
    // An unfinished URL is not a deployment classification.
  }
  return 'unknown'
}

// Configuration can be edited before authentication succeeds. It does not grant access.
export function resolveConfiguredServiceProvider(
  baseUrl: string,
  selection: ServiceSelection = 'auto',
): ServiceProvider {
  const detected = identifyServiceProvider(baseUrl)
  return detected === 'volcengine'
    ? detected
    : selection === 'auto'
      ? detected
      : selection
}
