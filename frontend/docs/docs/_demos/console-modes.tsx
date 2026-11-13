import { useMemo, useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { OpenVikingProvider } from '@openviking/react/client';
import { Console } from '@openviking/react/modules/console';
import type { ConsoleMode } from '@openviking/react/modules/console';
import '@openviking/react/modules/console/style.css';
import { botDemoService } from '#bot-demo-service';
import { consoleTransport } from '#console-transport';

const transport = consoleTransport();
const choices = {
  both: ['conversation', 'command'],
  conversation: ['conversation'],
  command: ['command'],
} as const satisfies Record<string, readonly ConsoleMode[]>;

export default function ConsoleModes() {
  const zh = useLang() === 'zh';
  const [choice, setChoice] = useState<keyof typeof choices>('command');
  const service = useMemo(() => botDemoService(), []);
  return (
    <>
      <label>
        {zh ? '启用功能：' : 'Enabled modes: '}
        <select
          value={choice}
          onChange={(event) =>
            setChoice(event.target.value as keyof typeof choices)
          }
        >
          <option value="both">{zh ? '两者' : 'Both'}</option>
          <option value="conversation">VikingBot</option>
          <option value="command">TUI</option>
        </select>
      </label>
      <OpenVikingProvider fetch={transport}>
        <div style={{ height: 640 }}>
          <Console
            locale={zh ? 'zh-CN' : 'en-US'}
            modes={choices[choice]}
            chatOptions={{ service }}
          />
        </div>
      </OpenVikingProvider>
    </>
  );
}
