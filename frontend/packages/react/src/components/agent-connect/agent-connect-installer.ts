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

export function getInstallerInstructions(
  name: string,
  harness: string,
  locale: AgentConnectLocale,
  apiKey: string,
  baseUrl: string,
) {
  const command =
    'curl -fsSL https://openviking.ai/install | bash -s -- --yes' +
    ` --lang ${locale} --harness ${harness},cli` +
    ` --url ${shellArgument(baseUrl)} --api-key ${shellArgument(apiKey)}`;
  return locale === 'zh'
    ? `帮我为 ${name} 安装 OpenViking 记忆插件，并带我开始使用。OpenViking 是为 Agent 提供的上下文数据库（https://github.com/volcengine/OpenViking），下面的命令和链接来自它的官方文档。

1. 运行官方安装器。它需要联网并写入我的主目录，如果被你的沙箱拦住，请向我申请批准：

   ${command}

2. 装好后读 OpenViking 的接入引导，按它带我完成设置。导入历史、写协作规则这类选择，引导会让你先问我。docs.openviking.ai 和 docs.openviking.net 是同一个文档站，哪个打得开用哪个：https://docs.openviking.ai/agents/zh/onboarding.md

API key 是我在 OpenViking 控制台给自己生成的，安装器会把它存到本机的 ~/.openviking/ovcli.conf。命令里原样使用就行，回复里不用再显示它，也不要写进别的文件或规则。全程用中文和我交流。`
    : `Install the OpenViking memory plugin for ${name} and help me start using it. OpenViking is a context database for AI agents (https://github.com/volcengine/OpenViking); the command and link below are from its official docs.

1. Run the official installer. It needs network access and writes to my home directory; if your sandbox blocks that, ask me for approval:

   ${command}

2. Once it's installed, read the OpenViking onboarding guide and use it to walk me through setup. The guide has you ask me before choices like importing history or adding a working rule. docs.openviking.ai and docs.openviking.net are the same docs site; use whichever loads: https://docs.openviking.ai/agents/en/onboarding.md

The API key is one I created for myself in the OpenViking console; the installer stores it in ~/.openviking/ovcli.conf on this machine. Use it in the command as it is; there's no need to show it in your replies, and don't write it into any other file or rule. Talk to me in English.`;
}
