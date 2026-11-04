import { useAppConnection } from './hooks/use-app-connection'
import type { ConnectionSnapshot } from './lib/connection-contract'

/** Business read model. Credentials and mutable profile configuration stay internal. */
export type ConnectionAccess = Pick<
  ConnectionSnapshot,
  | 'accessContext'
  | 'activeProfileId'
  | 'connectionRole'
  | 'identityScopeKey'
  | 'isConnectionRoleLoading'
  | 'isGatewayHost'
  | 'openvikingUrl'
  | 'serverHealth'
  | 'serverMode'
  | 'studioMode'
>

export type ConnectionCommands = Pick<
  ConnectionSnapshot,
  | 'openConnectionSettings'
  | 'reconnect'
  | 'switchIdentity'
  | 'switchManagementAccount'
>

export function useConnectionAccess(): ConnectionAccess {
  const context = useAppConnection()
  return {
    accessContext: context.accessContext,
    activeProfileId: context.activeProfileId,
    connectionRole: context.connectionRole,
    identityScopeKey: context.identityScopeKey,
    isConnectionRoleLoading: context.isConnectionRoleLoading,
    isGatewayHost: context.isGatewayHost,
    openvikingUrl: context.openvikingUrl,
    serverHealth: context.serverHealth,
    serverMode: context.serverMode,
    studioMode: context.studioMode,
  }
}
export function useConnectionCommands(): ConnectionCommands {
  const context = useAppConnection()
  return {
    openConnectionSettings: context.openConnectionSettings,
    reconnect: context.reconnect,
    switchIdentity: context.switchIdentity,
    switchManagementAccount: context.switchManagementAccount,
  }
}
