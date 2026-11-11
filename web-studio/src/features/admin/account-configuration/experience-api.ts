import { getOvResult, ovClient } from '#/shared/api/ov-client/index'

type AgentEvolutionStatus = { enabled: boolean; accountId?: string }

type AccountConfigurationResult = {
  account_id?: string
  settings: { agent_evolution: { enabled: boolean } }
}

function normalizeAgentEvolutionStatus(
  result: AccountConfigurationResult,
): AgentEvolutionStatus {
  return {
    enabled: result.settings.agent_evolution.enabled,
    accountId:
      typeof result.account_id === 'string' ? result.account_id : undefined,
  }
}

export async function fetchAgentEvolutionStatus(
  accountId: string,
  signal?: AbortSignal,
): Promise<AgentEvolutionStatus> {
  const result = await getOvResult<AccountConfigurationResult>(
    ovClient.client.get({
      signal,
      url: `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/configuration`,
    }),
  )
  return normalizeAgentEvolutionStatus(result)
}

export async function setAgentEvolutionEnabled(
  accountId: string,
  enabled: boolean,
): Promise<AgentEvolutionStatus> {
  const result = await getOvResult<AccountConfigurationResult>(
    ovClient.client.patch({
      body: { settings: { agent_evolution: { enabled } } },
      url: `/api/v1/admin/accounts/${encodeURIComponent(accountId)}/configuration`,
    }),
  )
  return normalizeAgentEvolutionStatus(result)
}
