import { useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { OpenVikingProvider } from '@openviking/react/client';
import { Console } from '@openviking/react/modules/console';
import '@openviking/react/modules/console/style.css';
import { consoleTransport } from '#console-transport';
import { useConsoleConversation } from '#console-conversation';

const transport = consoleTransport();

export default function ConsoleBasic() {
  const zh = useLang() === 'zh';
  const [opened, setOpened] = useState('');
  const conversation = useConsoleConversation(zh);
  return (
    <div>
      <p>
        {zh
          ? '模拟服务：不会访问真实数据。输入 ov help 查看命令。'
          : 'Simulated service: no real data is accessed. Enter ov help for commands.'}
      </p>
      <OpenVikingProvider fetch={transport}>
        <div
          style={{
            display: 'flex',
            flexDirection: 'column',
            height: 720,
            minWidth: 0,
          }}
        >
          <Console
            locale={zh ? 'zh' : 'en'}
            onOpenUri={setOpened}
            conversation={conversation}
          />
        </div>
      </OpenVikingProvider>
      <p role="status">
        {opened ? `${zh ? '打开资源' : 'Open resource'}: ${opened}` : ''}
      </p>
    </div>
  );
}
