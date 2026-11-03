import type {
  AuthMethod,
  KnownServiceProvider,
  LoginEntry,
  ServiceProvider,
  StudioEdition,
  StudioEnvironment,
} from '../studio-contract'
import type { WorkbenchEntry } from '../entry/paths'

export type StudioDeploymentConfig = {
  edition: StudioEdition
  environment: StudioEnvironment
  hostedApiBaseUrl: string
}

export type LoginService = {
  entry: Exclude<LoginEntry, 'email'>
  provider: KnownServiceProvider
  endpoint: { kind: 'custom' } | { kind: 'fixed'; url: string }
  keyHelp: 'apiLogin.keyHelp' | 'hostedKeyHelp'
  termsUrl: string | null
}

export type ApiKeyLoginConfig = {
  title: 'apiLogin.title' | 'apiLogin.cloudTitle'
  description: 'apiLogin.description' | 'apiLogin.cloudDescription'
  showEndpoint: boolean
  services: readonly [LoginService, ...LoginService[]]
}

export type StudioAuthConfig = {
  defaultPage: AuthMethod | 'welcome'
  allowEmailSwitch: boolean
  termsUrl: string
  apiKey: ApiKeyLoginConfig
}

// Product decisions only. A profile never grants runtime permissions.
export type StudioExperience = {
  edition: StudioEdition
  presentation: { filesystem: 'console' | 'studio' }
  auth: StudioAuthConfig
  preview: { enabled: boolean; selected: StudioEdition }
  connections:
    | { kind: 'selectable'; hostedEndpoint?: string }
    | { kind: 'fixed'; provider: KnownServiceProvider; endpoint: string }
  startup: { discoverGatewayHost: boolean }
  entry: {
    management: 'enabled' | 'disabled'
    workbench: Readonly<Record<ServiceProvider, WorkbenchEntry>>
    managementFallback: WorkbenchEntry
  }
}
