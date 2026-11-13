import type { ConnectionProfile } from '#/features/connection/lib/profiles'
import type { StudioMode } from '#/features/connection/lib/studio-mode'
import type { ConnectionDraft } from '#/features/connection/lib/types'

export type WorkbenchIdentity = Pick<
  ConnectionDraft,
  'accountId' | 'userId' | 'apiKey'
>

function identity(connection: ConnectionDraft): WorkbenchIdentity {
  return {
    accountId: connection.accountId,
    userId: connection.userId,
    apiKey: connection.apiKey,
  }
}

export function rememberSurface(
  profile: ConnectionProfile,
  connection: ConnectionDraft,
  mode: StudioMode,
): ConnectionProfile {
  return {
    ...profile,
    connection,
    ...(mode === 'workbench'
      ? { workbenchIdentity: identity(connection) }
      : {
          managementAccountId: connection.accountId,
          // Preserve a pre-upgrade data identity before the first management switch.
          ...(!profile.workbenchIdentity && connection.apiKey
            ? { workbenchIdentity: identity(connection) }
            : {}),
        }),
  }
}

export function restoreSurface(
  profile: ConnectionProfile,
  mode: StudioMode,
): ConnectionDraft {
  const connection = profile.connection
  const saved = profile.workbenchIdentity
  if (
    mode === 'workbench' &&
    saved &&
    ['accountId', 'userId', 'apiKey'].every(
      (key) => typeof saved[key as keyof WorkbenchIdentity] === 'string',
    )
  ) {
    return {
      ...connection,
      accountId: saved.accountId,
      userId: saved.userId,
      apiKey: saved.apiKey,
    }
  }
  const account = profile.managementAccountId
  if (
    mode === 'management' &&
    typeof account === 'string' &&
    account &&
    account !== connection.accountId
  ) {
    const user = [
      profile.workbenchIdentity,
      ...(profile.identities ?? []).filter(
        (entry) =>
          entry.baseUrl.trim().replace(/\/+$/, '') ===
            connection.baseUrl.trim().replace(/\/+$/, '') &&
          entry.serviceSelection === connection.serviceSelection,
      ),
    ].find((entry) => entry?.accountId === account)
    return {
      ...connection,
      accountId: account,
      userId: user?.userId ?? '',
      apiKey: user?.apiKey ?? '',
    }
  }
  return connection
}
