import { AgentConnect } from '@openviking/react/components/agent-connect';
import '@openviking/react/components/agent-connect/style.css';

export default function AgentConnectBasic() {
  return (
    <AgentConnect
      tabs={[{ id: 'mainstream', label: 'Mainstream agents' }]}
      activeTabId="mainstream"
      items={[]}
    />
  );
}
