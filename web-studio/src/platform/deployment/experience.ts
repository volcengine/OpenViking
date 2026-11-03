import { identifyServiceProvider } from '../connection/service-provider'
import {
  ServiceProvider,
  StudioEdition,
  StudioEnvironment,
} from '../studio-contract'
import { getStudioDeployment } from './config'
import { selfHostedExperience } from './editions/self-hosted'
import { volcengineExperience } from './editions/volcengine'
import type { StudioDeploymentConfig, StudioExperience } from './types'

const editions = {
  [StudioEdition.SelfHosted]: selfHostedExperience,
  [StudioEdition.Volcengine]: volcengineExperience,
} satisfies Record<
  StudioEdition,
  (config: StudioDeploymentConfig) => StudioExperience
>

export function resolveStudioExperience(
  config: StudioDeploymentConfig,
): StudioExperience {
  return editions[config.edition](config)
}

let startupExperience: StudioExperience | undefined

// Production selects its edition once, before rendering routes. The fallback
// keeps isolated consumers/tests usable without initializing the whole app.
export function resolveStudioStartup(
  config: StudioDeploymentConfig,
  previewSelection?: unknown,
): StudioExperience {
  if (config.environment !== StudioEnvironment.Staging)
    return resolveStudioExperience(config)
  // Domain STG starts in the official experience. A tab-local preview can select
  // the open-source experience; the release environment and endpoint stay STG.
  const edition =
    previewSelection === StudioEdition.SelfHosted
      ? StudioEdition.SelfHosted
      : StudioEdition.Volcengine
  return resolveStudioExperience({ ...config, edition })
}

export function initializeStudioExperience(
  previewSelection?: unknown,
): StudioExperience {
  return (startupExperience ??= resolveStudioStartup(
    getStudioDeployment(),
    previewSelection,
  ))
}

export function getStudioExperience(): StudioExperience {
  return startupExperience ?? resolveStudioExperience(getStudioDeployment())
}

export function isStudioConnectionAllowed(
  connection: { baseUrl: string; serviceSelection?: string },
  experience = getStudioExperience(),
): boolean {
  const policy = experience.connections
  if (
    policy.kind === 'selectable' &&
    (!policy.hostedEndpoint ||
      (connection.serviceSelection !== ServiceProvider.Volcengine &&
        identifyServiceProvider(connection.baseUrl) !==
          ServiceProvider.Volcengine))
  )
    return true
  try {
    return (
      connection.serviceSelection !== ServiceProvider.OpenSource &&
      new URL(connection.baseUrl).href.replace(/\/+$/, '') ===
        (policy.kind === 'fixed' ? policy.endpoint : policy.hostedEndpoint)
    )
  } catch {
    return false
  }
}

// This is the existing native administration support policy. Hosted ROOT
// routing/management is intentionally pending a separate integration.
export function supportsNativeManagement(
  provider: ServiceProvider,
  experience = getStudioExperience(),
): boolean {
  return (
    experience.entry.management === 'enabled' &&
    provider === ServiceProvider.OpenSource
  )
}

export function managementUnavailableDestination(
  experience = getStudioExperience(),
) {
  return experience.entry.management === 'disabled'
    ? experience.entry.managementFallback
    : null
}
