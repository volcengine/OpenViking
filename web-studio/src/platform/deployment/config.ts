import { StudioEdition, StudioEnvironment } from '../studio-contract'
import type { StudioDeploymentConfig } from './types'

const HOSTED_API_URLS = {
  [StudioEnvironment.Production]:
    'https://api.vikingdb.cn-beijing.volces.com/openviking',
  [StudioEnvironment.Staging]:
    'https://api.vikingdb-stg.cn-beijing.volces.com/openviking',
} satisfies Record<StudioEnvironment, string>

declare global {
  interface Window {
    __OV_STUDIO_CONFIG__?: Partial<StudioDeploymentConfig>
  }
}

// Deployment metadata is not an authorization credential. Never infer the
// edition from location.hostname: self-hosted installations also use domains.
export function resolveStudioDeployment(
  input: {
    edition?: unknown
    environment?: unknown
    basePath?: unknown
    hostedApiBaseUrl?: unknown
  } = {},
): StudioDeploymentConfig {
  const edition = input.edition ?? StudioEdition.SelfHosted
  if (
    edition !== StudioEdition.SelfHosted &&
    edition !== StudioEdition.Volcengine
  )
    // eslint-disable-next-line studio-i18n/no-literal-message -- Internal diagnostic; startup renders a localized deployment error.
    throw new Error('Invalid Studio edition')
  // The deployment wrapper passes /workbench-stg/ as Vite's router base.
  // Select this once at startup; business pages never inspect the prefix.
  const environment =
    input.environment ??
    (typeof input.basePath === 'string' &&
    /^\/workbench-stg\/?$/.test(input.basePath)
      ? StudioEnvironment.Staging
      : StudioEnvironment.Production)
  if (
    environment !== StudioEnvironment.Production &&
    environment !== StudioEnvironment.Staging
  )
    // eslint-disable-next-line studio-i18n/no-literal-message -- Internal startup diagnostic.
    throw new Error('Invalid Studio environment')
  const address = input.hostedApiBaseUrl ?? HOSTED_API_URLS[environment]
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
  return {
    edition,
    environment,
    hostedApiBaseUrl: url.href.replace(/\/+$/, ''),
  }
}

export function getStudioDeployment(): StudioDeploymentConfig {
  const runtime =
    typeof window === 'undefined' ? undefined : window.__OV_STUDIO_CONFIG__
  return resolveStudioDeployment({
    edition: runtime?.edition ?? import.meta.env.VITE_STUDIO_EDITION,
    environment:
      runtime?.environment ?? import.meta.env.VITE_STUDIO_ENVIRONMENT,
    basePath: import.meta.env.BASE_URL,
    hostedApiBaseUrl:
      runtime?.hostedApiBaseUrl ?? import.meta.env.VITE_STUDIO_HOSTED_API_URL,
  })
}
