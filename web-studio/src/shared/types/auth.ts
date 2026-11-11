export type ServerAuthMode = 'api_key' | 'trusted' | 'dev' | 'oidc' | 'ldap'
export type ServerMode = ServerAuthMode | 'checking' | 'offline'
