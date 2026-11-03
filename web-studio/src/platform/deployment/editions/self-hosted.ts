import {
  AuthMethod,
  LoginEntry,
  ServiceProvider,
  StudioEdition,
  StudioEnvironment,
} from '../../studio-contract'
import { StudioEntry } from '../../entry/paths'
import type { StudioDeploymentConfig, StudioExperience } from '../types'

export function selfHostedExperience(
  config: StudioDeploymentConfig,
): StudioExperience {
  return {
    edition: StudioEdition.SelfHosted,
    preview: {
      enabled: config.environment === StudioEnvironment.Staging,
      selected: StudioEdition.SelfHosted,
    },
    presentation: { filesystem: 'studio' },
    auth: {
      defaultPage: AuthMethod.ApiKey,
      allowEmailSwitch: false,
      termsUrl:
        'https://docs.volcengine.com/docs/vector-database-vikingdb/openviking-personal-product-service-terms',
      apiKey: {
        title: 'apiLogin.title',
        description: 'apiLogin.description',
        showEndpoint: true,
        services: [
          {
            entry: LoginEntry.VolcengineApiKey,
            provider: ServiceProvider.Volcengine,
            endpoint: { kind: 'fixed', url: config.hostedApiBaseUrl },
            keyHelp: 'hostedKeyHelp',
            termsUrl:
              'https://docs.volcengine.com/docs/vector-database-vikingdb/openviking-personal-product-service-terms',
          },
          {
            entry: LoginEntry.OpenVikingApiKey,
            provider: ServiceProvider.OpenSource,
            endpoint: { kind: 'custom' },
            keyHelp: 'apiLogin.keyHelp',
            termsUrl: null,
          },
        ],
      },
    },
    connections: {
      kind: 'selectable',
      ...(config.environment === StudioEnvironment.Staging
        ? { hostedEndpoint: config.hostedApiBaseUrl }
        : {}),
    },
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
