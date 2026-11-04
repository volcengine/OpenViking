// These dimensions are independent. Keep persisted/wire values stable.
export const StudioEdition = {
  SelfHosted: 'self-hosted',
  Volcengine: 'volcengine',
} as const
export type StudioEdition = (typeof StudioEdition)[keyof typeof StudioEdition]

// Release environment is independent of the product edition.
export const StudioEnvironment = {
  Production: 'production',
  Staging: 'stg',
} as const
export type StudioEnvironment =
  (typeof StudioEnvironment)[keyof typeof StudioEnvironment]

export const ServiceProvider = {
  OpenSource: 'opensource',
  Volcengine: 'volcengine',
  Unknown: 'unknown',
} as const
export type ServiceProvider =
  (typeof ServiceProvider)[keyof typeof ServiceProvider]
export type KnownServiceProvider = Exclude<
  ServiceProvider,
  typeof ServiceProvider.Unknown
>
export type ServiceSelection = 'auto' | KnownServiceProvider

export const AuthMethod = { ApiKey: 'api-key', Email: 'email' } as const
export type AuthMethod = (typeof AuthMethod)[keyof typeof AuthMethod]

export const LoginEntry = {
  Email: 'email',
  VolcengineApiKey: 'volcengine-api-key',
  OpenVikingApiKey: 'openviking-api-key',
} as const
export type LoginEntry = (typeof LoginEntry)[keyof typeof LoginEntry]

export const StudioSurface = {
  Auth: 'auth',
  Workbench: 'workbench',
  Management: 'management',
} as const
export type StudioSurface = (typeof StudioSurface)[keyof typeof StudioSurface]
export type StudioMode = Exclude<StudioSurface, typeof StudioSurface.Auth>

export const ConnectionRole = {
  Root: 'root',
  Admin: 'admin',
  User: 'user',
  Unknown: 'unknown',
} as const
export type ConnectionRole =
  (typeof ConnectionRole)[keyof typeof ConnectionRole]

// Server auth modes are wire protocol values, not UI login methods.
export const ServerAuthMode = {
  ApiKey: 'api_key',
  Trusted: 'trusted',
  Development: 'dev',
  Oidc: 'oidc',
  Ldap: 'ldap',
} as const
export type ServerAuthMode =
  (typeof ServerAuthMode)[keyof typeof ServerAuthMode]
export type ServerMode = ServerAuthMode | 'checking' | 'offline'
