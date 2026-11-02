import {
  AuthMethod,
  LoginEntry,
  ServiceProvider,
  StudioEdition,
} from '../../studio-contract'
import { StudioEntry } from '../../entry/paths'
import type { StudioDeploymentConfig, StudioExperience } from '../types'

export function volcengineExperience(
  config: StudioDeploymentConfig,
): StudioExperience {
  return {
    edition: StudioEdition.Volcengine,
    auth: {
      defaultMethod: AuthMethod.Email,
      apiKey: {
        title: 'apiLogin.cloudTitle',
        description: 'apiLogin.cloudDescription',
        services: [
          {
            entry: LoginEntry.VolcengineApiKey,
            provider: ServiceProvider.Volcengine,
            endpoint: { kind: 'fixed', url: config.hostedApiBaseUrl },
            keyHelp: 'hostedKeyHelp',
          },
        ],
      },
    },
    connections: {
      kind: 'fixed',
      provider: ServiceProvider.Volcengine,
      endpoint: config.hostedApiBaseUrl,
    },
    startup: { discoverGatewayHost: false },
    entry: {
      management: 'disabled',
      workbench: {
        [ServiceProvider.OpenSource]: StudioEntry.Filesystem,
        [ServiceProvider.Volcengine]: StudioEntry.Filesystem,
        [ServiceProvider.Unknown]: StudioEntry.Filesystem,
      },
      managementFallback: StudioEntry.Filesystem,
    },
  }
}
