import {
  LoginEntry,
  ServiceProvider,
  StudioEdition,
  StudioEnvironment,
} from '../../studio-contract'
import { StudioEntry } from '../../entry/paths'
import type { StudioDeploymentConfig, StudioExperience } from '../types'

export function volcengineExperience(
  config: StudioDeploymentConfig,
): StudioExperience {
  return {
    edition: StudioEdition.Volcengine,
    preview: {
      enabled: config.environment === StudioEnvironment.Staging,
      selected: StudioEdition.Volcengine,
    },
    auth: {
      defaultPage: 'welcome',
      allowEmailSwitch: true,
      termsUrl:
        'https://docs.volcengine.com/docs/vector-database-vikingdb/viking-product-service-terms',
      apiKey: {
        title: 'apiLogin.cloudTitle',
        description: 'apiLogin.cloudDescription',
        showEndpoint: false,
        services: [
          {
            entry: LoginEntry.VolcengineApiKey,
            provider: ServiceProvider.Volcengine,
            endpoint: { kind: 'fixed', url: config.hostedApiBaseUrl },
            keyHelp: 'hostedKeyHelp',
            termsUrl:
              'https://docs.volcengine.com/docs/vector-database-vikingdb/viking-product-service-terms',
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
