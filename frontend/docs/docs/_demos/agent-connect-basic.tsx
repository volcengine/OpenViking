import { useLang } from '@rspress/core/runtime';
import { AgentConnect } from '@openviking/react/components/agent-connect';
import '@openviking/react/components/agent-connect/style.css';

export default function AgentConnectBasic() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  return <AgentConnect apiKey="demo-api-key" locale={locale} />;
}
