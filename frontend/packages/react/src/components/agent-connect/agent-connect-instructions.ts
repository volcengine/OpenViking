import contentZh from './content-zh.js';
import contentEn from './content-en.js';
import type { AgentConnectLocale } from './types.js';

export const DEFAULT_BASE_URL =
  'https://api.vikingdb.cn-beijing.volces.com/openviking';

const installerAgents = new Set([
  'codex',
  'claude-code',
  'trae',
  'cursor',
  'opencode',
  'deepseek-harness',
  'pi',
  'zcode',
  'kimi-code',
]);

/** 沿用参考组件的官方安装器提示，文案内置，语言和凭据由宿主决定。 */
const installerInstructions = {
  zh: `帮我安装 OpenViking 记忆插件，并带我开始使用。OpenViking 是为 Agent 提供的上下文数据库（https://github.com/volcengine/OpenViking），下面的命令和链接来自它的官方文档。

1. 运行官方安装器。它需要联网并写入我的主目录，如果被你的沙箱拦住，请向我申请批准：

   curl -fsSL https://openviking.ai/install | bash -s -- --yes --lang zh --url {{OPENVIKING_BASE_URL}} --api-key <API_KEY>

2. 装好后读 OpenViking 的接入引导，按它带我完成设置。导入历史、写协作规则这类选择，引导会让你先问我。docs.openviking.ai 和 docs.openviking.net 是同一个文档站，哪个打得开用哪个：https://docs.openviking.ai/agents/zh/onboarding.md

API key 是我在 OpenViking 控制台给自己生成的，安装器会把它存到本机的 ~/.openviking/ovcli.conf。命令里原样使用就行，回复里不用再显示它，也不要写进别的文件或规则。全程用中文和我交流。`,
  en: `Install the OpenViking memory plugin and help me start using it. OpenViking is a context database for AI agents (https://github.com/volcengine/OpenViking); the command and link below are from its official docs.

1. Run the official installer. It needs network access and writes to my home directory; if your sandbox blocks that, ask me for approval:

   curl -fsSL https://openviking.ai/install | bash -s -- --yes --lang en --url {{OPENVIKING_BASE_URL}} --api-key <API_KEY>

2. Once it's installed, read the OpenViking onboarding guide and use it to walk me through setup. The guide has you ask me before choices like importing history or adding a working rule. docs.openviking.ai and docs.openviking.net are the same docs site; use whichever loads: https://docs.openviking.ai/agents/en/onboarding.md

The API key is one I created for myself in the OpenViking console; the installer stores it in ~/.openviking/ovcli.conf on this machine. Use it in the command as it is; there's no need to show it in your replies, and don't write it into any other file or rule. Talk to me in English.`,
};

export function getAgentConnectContent(locale: AgentConnectLocale) {
  return locale === 'en' ? contentEn : contentZh;
}

/** 使用回调替换，密钥中的 $& 等字符也保持原值；不递归替换注入值。 */
export function getAgentConnectInstructions(
  id: keyof typeof contentZh,
  locale: AgentConnectLocale,
  apiKey: string,
  baseUrl: string,
  guideId?: string,
) {
  const fixed = installerAgents.has(id);
  const content = getAgentConnectContent(locale)[id];
  const guide = content.guides.find((item) => item.id === guideId);
  let template = fixed
    ? installerInstructions[locale]
    : (guide?.markdown ?? content.markdown);
  // Hermes/OpenClaw 的向导正文没有密钥占位符，补上当前调用方的凭据。
  if (!fixed && !/\{\{OPENVIKING_API_KEY/.test(template)) {
    template +=
      '\n\nBase URL: {{OPENVIKING_BASE_URL}}\n\nAPI Key:\n\n{{OPENVIKING_API_KEY_BLOCK}}';
  }
  const url = baseUrl.replace(/\/+$/, '');
  function hydrate(key: string) {
    return template.replace(
      /\{\{OPENVIKING_BASE_URL\}\}|\{\{OPENVIKING_API_KEY_BLOCK\}\}|\{\{OPENVIKING_API_KEY\}\}|<API_KEY>|https:\/\/api\.vikingdb\.cn-beijing\.volces\.com\/openviking/g,
      (token) => {
        if (token === '{{OPENVIKING_API_KEY_BLOCK}}') {
          const value = id === 'deerflow' ? `OPENVIKING_API_KEY=${key}` : key;
          return `\`\`\`text\n${value}\n\`\`\``;
        }
        if (token.includes('API_KEY')) return key;
        return url;
      },
    );
  }
  return {
    fixed,
    display: hydrate('••••••••'),
    copy: hydrate(apiKey),
  };
}
