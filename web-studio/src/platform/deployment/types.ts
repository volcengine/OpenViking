import type {
  AuthMethod,
  KnownServiceProvider,
  LoginEntry,
  ServiceProvider,
  StudioEdition,
} from '../studio-contract'
import type { WorkbenchEntry } from '../entry/paths'

export type StudioDeploymentConfig = {
  edition: StudioEdition
  hostedApiBaseUrl: string
}

export type LoginService = {
  entry: Exclude<LoginEntry, 'email'>
  provider: KnownServiceProvider
  endpoint: { kind: 'custom' } | { kind: 'fixed'; url: string }
  keyHelp: 'apiLogin.keyHelp' | 'hostedKeyHelp'
}

export type ApiKeyLoginConfig = {
  title: 'apiLogin.title' | 'apiLogin.cloudTitle'
  description: 'apiLogin.description' | 'apiLogin.cloudDescription'
  services: readonly [LoginService, ...LoginService[]]
}

// Product decisions only. A profile never grants runtime permissions.
export type StudioExperience = {
  edition: StudioEdition
  auth: { defaultMethod: AuthMethod; apiKey: ApiKeyLoginConfig }
  connections:
    | { kind: 'selectable' }
    | { kind: 'fixed'; provider: KnownServiceProvider; endpoint: string }
  startup: { discoverGatewayHost: boolean }
  entry: {
    management: 'enabled' | 'disabled'
    workbench: Readonly<Record<ServiceProvider, WorkbenchEntry>>
    managementFallback: WorkbenchEntry
  }
}
