import type enUS from './en-US.js';

const messages: typeof enUS = {
  title: '连接 Agent',
  mainstream: '接入主流 Agent',
  custom: '开发自己的 Agent',
  integration: '接入',
  instructions: '完整指令',
  copy: '复制并发送给 Agent',
  copyMarkdown: '复制为 Markdown',
  copied: '已复制',
  failed: '复制失败，请重试',
  notice:
    '接入后 Agent 能读到你在 OpenViking 里能看到的内容，若接入境外服务数据有可能传到境外。',
  seeAgreement: '详见',
  agreement: '《用户协议》',
  apiKey: 'API Key',
  overseas: '境外',
  hermesCustomIntro:
    '# 为 Hermes 配置自定义 OpenViking 服务\n\n连接调用方指定的 OpenViking 服务，使用以下步骤填写服务地址和 API Key。\n\n',
  hermesCustomStep:
    '3. 选择 **Custom**，服务地址填写 {{OPENVIKING_BASE_URL}}。',
  installerInstructions: (
    name: string,
    command: string,
  ) => `帮我为 ${name} 安装 OpenViking 记忆插件，并带我开始使用。OpenViking 是为 Agent 提供的上下文数据库（https://github.com/volcengine/OpenViking），下面的命令和链接来自它的官方文档。

1. 运行官方安装器。它需要联网并写入我的主目录，如果被你的沙箱拦住，请向我申请批准：

   ${command}

2. 装好后读 OpenViking 的接入引导，按它带我完成设置。导入历史、写协作规则这类选择，引导会让你先问我。docs.openviking.ai 和 docs.openviking.net 是同一个文档站，哪个打得开用哪个：https://docs.openviking.ai/agents/zh/onboarding.md

API key 是我在 OpenViking 控制台给自己生成的，安装器会把它存到本机的 ~/.openviking/ovcli.conf。命令里原样使用就行，回复里不用再显示它，也不要写进别的文件或规则。全程用中文和我交流。`,
};
export default messages;
