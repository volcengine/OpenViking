import { getOvResult, ovClient } from '#/shared/api/ov-client/index'

type AgentEvolutionStatus = { enabled: boolean; accountId?: string }

type AccountSettingsResult = {
  account_id?: string
  settings?: { agent_evolution?: { enabled?: boolean } }
}

function normalizeAgentEvolutionStatus(
  result: AccountSettingsResult,
): AgentEvolutionStatus {
  return {
    enabled: result.settings?.agent_evolution?.enabled === true,
    accountId:
      typeof result.account_id === 'string' ? result.account_id : undefined,
  }
}

export async function fetchAgentEvolutionStatus(
  accountId: string,
  signal?: AbortSignal,
): Promise<AgentEvolutionStatus> {
  const result = await getOvResult<AccountSettingsResult>(
    ovClient.client.get({
      signal,
      url: `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/settings`,
    }),
  )
  return normalizeAgentEvolutionStatus(result)
}

export async function setAgentEvolutionEnabled(
  accountId: string,
  enabled: boolean,
): Promise<AgentEvolutionStatus> {
  const result = await getOvResult<AccountSettingsResult>(
    ovClient.client.patch({
      body: { agent_evolution: { enabled } },
      url: `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/settings`,
    }),
  )
  return normalizeAgentEvolutionStatus(result)
}
