import contentZh from './content-zh.js';
import contentEn from './content-en.js';
import type { AgentConnectLocale } from './types.js';
import {
  getInstallerInstructions,
  INSTALLER_HARNESSES,
} from './agent-connect-installer.js';

export const DEFAULT_BASE_URL =
  'https://api.vikingdb.cn-beijing.volces.com/openviking';

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
  const content = getAgentConnectContent(locale)[id];
  const harness = INSTALLER_HARNESSES[id];
  if (harness) {
    const instructions = (key: string) =>
      getInstallerInstructions(content.name, harness, locale, key, baseUrl);
    return {
      fixed: true,
      display: instructions('••••••••'),
      copy: instructions(apiKey),
    };
  }
  const guide = content.guides.find((item) => item.id === guideId);
  let template = guide?.markdown ?? content.markdown;
  if (id === 'hermes' && baseUrl.replace(/\/+$/, '') !== DEFAULT_BASE_URL) {
    // 自定义连接不要求开通云服务；保留后续 profile 与插件操作步骤。
    template = template.replace(/^[\s\S]*?(?=^## )/m, () =>
      locale === 'zh'
        ? '# 为 Hermes 配置自定义 OpenViking 服务\n\n连接调用方指定的 OpenViking 服务，使用以下步骤填写服务地址和 API Key。\n\n'
        : '# Custom OpenViking server for Hermes\n\nConnect to the host-provided OpenViking server using the URL and API key in the setup steps below.\n\n',
    );
    template = template.replace(
      /^3\. (?:选择|Choose) \*\*OpenViking Service \(VolcEngine Cloud\)\*\*[。.][ \t]*$/m,
      () =>
        locale === 'zh'
          ? '3. 选择 **Custom**，服务地址填写 {{OPENVIKING_BASE_URL}}。'
          : '3. Choose **Custom** and enter {{OPENVIKING_BASE_URL}} as the server URL.',
    );
  }
  // Hermes/OpenClaw 的向导正文没有密钥占位符，补上当前调用方的凭据。
  if (!/\{\{OPENVIKING_API_KEY/.test(template)) {
    template +=
      '\n\nBase URL: {{OPENVIKING_BASE_URL}}\n\nAPI Key:\n\n{{OPENVIKING_API_KEY_BLOCK}}';
  }
  const pathBaseUrl = baseUrl.replace(/\/+$/, '');
  function hydrate(key: string) {
    return template.replace(
      /\{\{OPENVIKING_BASE_URL\}\}|\{\{OPENVIKING_API_KEY_BLOCK\}\}|\{\{OPENVIKING_API_KEY\}\}|<API_KEY>|https:\/\/api\.vikingdb\.cn-beijing\.volces\.com\/openviking/g,
      (token, offset: number) => {
        if (token === '{{OPENVIKING_API_KEY_BLOCK}}') {
          const value = id === 'deerflow' ? `OPENVIKING_API_KEY=${key}` : key;
          return `\`\`\`text\n${value}\n\`\`\``;
        }
        if (token.includes('API_KEY')) return key;
        // API 先赋值 url 再拼接路径；其余指南只在占位符后跟 / 时归一化。
        return id === 'api' || template[offset + token.length] === '/'
          ? pathBaseUrl
          : baseUrl;
      },
    );
  }
  return {
    fixed: false,
    display: hydrate('••••••••'),
    copy: hydrate(apiKey),
  };
}
