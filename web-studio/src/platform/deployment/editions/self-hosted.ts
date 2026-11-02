import {
  AuthMethod,
  LoginEntry,
  ServiceProvider,
  StudioEdition,
} from '../../studio-contract'
import { StudioEntry } from '../../entry/paths'
import type { StudioDeploymentConfig, StudioExperience } from '../types'

export function selfHostedExperience(
  config: StudioDeploymentConfig,
): StudioExperience {
  return {
    edition: StudioEdition.SelfHosted,
    auth: {
      defaultMethod: AuthMethod.ApiKey,
      apiKey: {
        title: 'apiLogin.title',
        description: 'apiLogin.description',
        services: [
          {
            entry: LoginEntry.OpenVikingApiKey,
            provider: ServiceProvider.OpenSource,
            endpoint: { kind: 'custom' },
            keyHelp: 'apiLogin.keyHelp',
          },
          {
            entry: LoginEntry.VolcengineApiKey,
            provider: ServiceProvider.Volcengine,
            endpoint: { kind: 'fixed', url: config.hostedApiBaseUrl },
            keyHelp: 'hostedKeyHelp',
          },
        ],
      },
    },
    connections: { kind: 'selectable' },
    startup: { discoverGatewayHost: true },
    entry: {
      management: 'enabled',
      workbench: {
        [ServiceProvider.OpenSource]: StudioEntry.Home,
        [ServiceProvider.Volcengine]: StudioEntry.Filesystem,
        [ServiceProvider.Unknown]: StudioEntry.Filesystem,
      },
      managementFallback: StudioEntry.Filesystem,
    },
  }
}
