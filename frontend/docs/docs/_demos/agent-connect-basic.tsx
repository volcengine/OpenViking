import { useLang } from '@rspress/core/runtime';
import { Checkbox } from '#demo-controls';
import { useState } from 'react';
import { AgentConnect } from '@openviking/react/components/agent-connect';
import '@openviking/react/components/agent-connect/style.css';

export default function AgentConnectBasic() {
  const locale = useLang() === 'zh' ? 'zh-CN' : 'en-US';
  const [isEnterprise, setIsEnterprise] = useState(false);
  return (
    <>
      <Checkbox checked={isEnterprise} onChange={setIsEnterprise}>
        {locale === 'zh-CN' ? '企业版数据库' : 'Enterprise database'}
      </Checkbox>
      <AgentConnect
        apiKey="demo-api-key"
        locale={locale}
        isEnterprise={isEnterprise}
      />
    </>
  );
}
