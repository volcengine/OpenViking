import { useMemo, useState } from 'react';
import { useLang } from '@rspress/core/runtime';
import { Console } from '@openviking/react/modules/console';
import '@openviking/react/modules/console/style.css';
import { botDemoService } from '#bot-demo-service';

export default function ConsoleChat() {
  const [edition, setEdition] = useState<'native' | 'commercial'>('native');
  const service = useMemo(() => botDemoService(edition), [edition]);
  const zh = useLang() === 'zh';
  return (
    <>
      <label>
        {zh ? '模拟服务：' : 'Simulated service: '}
        <select
          value={edition}
          onChange={(event) => setEdition(event.target.value as typeof edition)}
        >
          <option value="native">
            {zh ? '开源协议（中断与追问）' : 'Native (interrupt and follow-up)'}
          </option>
          <option value="commercial">
            {zh
              ? '火山协议（不提供这两项能力）'
              : 'Commercial (neither capability)'}
          </option>
        </select>
      </label>
      <div style={{ height: 640 }}>
        <Console
          locale={zh ? 'zh-CN' : 'en-US'}
          scopeKey={edition}
          chatOptions={{ service }}
        />
      </div>
    </>
  );
}
