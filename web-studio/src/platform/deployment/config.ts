import { StudioEdition } from '../studio-contract'
import type { StudioDeploymentConfig } from './types'

const DEFAULT_HOSTED_URL =
  'https://api.vikingdb.cn-beijing.volces.com/openviking'

declare global {
  interface Window {
    __OV_STUDIO_CONFIG__?: Partial<StudioDeploymentConfig>
  }
}

// Deployment metadata is not an authorization credential. Never infer the
// edition from location.hostname: self-hosted installations also use domains.
export function resolveStudioDeployment(
  input: { edition?: unknown; hostedApiBaseUrl?: unknown } = {},
): StudioDeploymentConfig {
  const edition = input.edition ?? StudioEdition.SelfHosted
  if (
    edition !== StudioEdition.SelfHosted &&
    edition !== StudioEdition.Volcengine
  )
    // eslint-disable-next-line studio-i18n/no-literal-message -- Internal diagnostic; startup renders a localized deployment error.
    throw new Error('Invalid Studio edition')
  const address = input.hostedApiBaseUrl ?? DEFAULT_HOSTED_URL
  // eslint-disable-next-line studio-i18n/no-literal-message -- Internal diagnostic; startup renders a localized deployment error.
  if (typeof address !== 'string') throw new Error('Invalid hosted endpoint')
  const url = new URL(address)
  if (
    url.protocol !== 'https:' ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    // eslint-disable-next-line studio-i18n/no-literal-message -- Internal diagnostic; startup renders a localized deployment error.
    throw new Error('Invalid hosted endpoint')
  return { edition, hostedApiBaseUrl: url.href.replace(/\/+$/, '') }
}

export function getStudioDeployment(): StudioDeploymentConfig {
  const runtime =
    typeof window === 'undefined' ? undefined : window.__OV_STUDIO_CONFIG__
  return resolveStudioDeployment({
    edition: runtime?.edition ?? import.meta.env.VITE_STUDIO_EDITION,
    hostedApiBaseUrl:
      runtime?.hostedApiBaseUrl ?? import.meta.env.VITE_STUDIO_HOSTED_API_URL,
  })
}
