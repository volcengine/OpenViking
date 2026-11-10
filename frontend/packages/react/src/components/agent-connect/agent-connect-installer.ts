import { resolveAgentConnectLabels } from './agent-connect-copy.js';
import type { AgentConnectLocale } from './types.js';

export const INSTALLER_HARNESSES: Partial<Record<string, string>> = {
  codex: 'codex',
  'claude-code': 'claude',
  trae: 'trae',
  cursor: 'cursor',
  opencode: 'opencode',
  'deepseek-harness': 'dsh',
  pi: 'pi',
  zcode: 'zcode',
  'kimi-code': 'kimicode',
};

/** POSIX Shell 单引号参数；不展开变量、命令替换或通配符。 */
function shellArgument(value: string) {
  return `'${value.replace(/'/g, "'\"'\"'")}'`;
}

export function getInstallerCommand(
  harness: string,
  locale: AgentConnectLocale,
  apiKey: string,
  baseUrl: string,
) {
  return (
    'curl -fsSL https://openviking.ai/install | bash -s -- --yes' +
    ` --lang ${locale === 'zh-CN' ? 'zh' : 'en'} --harness ${harness},cli` +
    ` --url ${shellArgument(baseUrl)} --api-key ${shellArgument(apiKey)}`
  );
}

export function getInstallerInstructions(
  name: string,
  harness: string,
  locale: AgentConnectLocale,
  apiKey: string,
  baseUrl: string,
) {
  const command = getInstallerCommand(harness, locale, apiKey, baseUrl);
  return resolveAgentConnectLabels(locale).installerInstructions(name, command);
}
